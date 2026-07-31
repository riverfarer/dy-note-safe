#!/usr/bin/env python3
"""Fetch Douyin comments through an optional local browser bridge.

The script never reads browser profile files or cookies. All network requests
run inside the already-authorized browser page via Runtime.evaluate.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import browser_bridge

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
CSV_FIELDS = [
    "row_index",
    "level",
    "parent_cid",
    "cid",
    "aweme_id",
    "create_time",
    "create_time_iso",
    "create_time_cn",
    "ip_label",
    "nickname",
    "uid",
    "sec_uid",
    "user_url",
    "digg_count",
    "reply_comment_total",
    "reply_to_reply_id",
    "reply_to_userid",
    "reply_to_username",
    "label_text",
    "is_hot",
    "status",
    "text",
]


class DouyinCommentError(RuntimeError):
    pass


BRIDGE = browser_bridge.BrowserBridge(browser_bridge.DEFAULT_ENDPOINT)


def configure_browser_bridge(endpoint: str | None) -> None:
    global BRIDGE
    try:
        BRIDGE = browser_bridge.BrowserBridge(endpoint)
    except browser_bridge.BrowserBridgeError as exc:
        raise DouyinCommentError(str(exc)) from exc


def progress(enabled: bool, phase: str, **data: Any) -> None:
    if enabled:
        print(json.dumps({"phase": phase, **data}, ensure_ascii=False), file=sys.stderr, flush=True)


def reply_total(comment: dict[str, Any]) -> int:
    try:
        return int(comment.get("reply_comment_total", comment.get("replyTotal", 0)) or 0)
    except (TypeError, ValueError):
        return 0


def is_normalized_row(row: dict[str, Any]) -> bool:
    return "level" in row and "cid" in row and "text" in row and ("nickname" in row or "parent_cid" in row)


def main_comments_capped(pages: list[dict[str, Any]]) -> bool:
    return any(bool(page.get("capped_by_max_main_comments")) for page in pages)


def main_comments_incomplete(pages: list[dict[str, Any]]) -> bool:
    return any(
        bool(page.get("truncated_by_page_limit") or page.get("cursor_stalled") or page.get("error"))
        for page in pages
    )


def replies_incomplete(reply_pages: list[dict[str, Any]]) -> bool:
    return any(
        bool(page.get("truncated_by_page_limit") or page.get("cursor_stalled") or page.get("error"))
        for page in reply_pages
    )


def output_label(no_replies: bool, is_sample: bool) -> str:
    if no_replies:
        return "main_only_sample" if is_sample else "main_only_full"
    return "sample" if is_sample else "full"


def http_json(method: str, path: str, body: str | None = None, timeout: int = 30) -> Any:
    try:
        return BRIDGE.json(method, path, body, timeout)
    except browser_bridge.BrowserBridgeError as exc:
        raise DouyinCommentError(str(exc)) from exc


def eval_js(target: str, js: str, timeout: int = 30) -> Any:
    try:
        return BRIDGE.evaluate(target, js, timeout)
    except browser_bridge.BrowserBridgeError as exc:
        raise DouyinCommentError(str(exc)) from exc


def open_target(url: str) -> str:
    try:
        return BRIDGE.open_target(url, timeout=45)
    except browser_bridge.BrowserBridgeError as exc:
        raise DouyinCommentError(str(exc)) from exc


def close_target(target: str) -> None:
    BRIDGE.close_target(target)


def extract_aweme_id(url: str) -> str:
    match = re.search(r"/(?:note|video)/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"(?:aweme_id|modal_id|item_id|group_id)=(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"(\d{16,22})", url)
    if match:
        return match.group(1)
    raise DouyinCommentError(f"Could not infer Douyin aweme id from URL: {url}")


def find_comment_base_url(target: str, retries: int = 8, delay: float = 1.0) -> str:
    js = r"""
(() => {
  const urls = performance.getEntriesByType('resource').map(e => e.name).reverse();
  const observed = urls.find(u => u.includes('/aweme/v1/web/comment/list/')) || '';
  if (!observed) return '';
  const u = new URL(observed);
  return `${u.origin}${u.pathname}`;
})()
"""
    for _ in range(retries):
        base = eval_js(target, js, timeout=10)
        if base:
            return str(base)
        time.sleep(delay)
    raise DouyinCommentError(
        "No Douyin comment/list request was observed. Open the page with web-access, "
        "make sure the comment panel is visible, then rerun with --target."
    )


def fetch_page(target: str, js: str) -> dict[str, Any]:
    value = eval_js(target, js, timeout=20)
    if isinstance(value, str):
        data = json.loads(value)
    elif isinstance(value, dict):
        data = value
    else:
        raise DouyinCommentError(f"Unexpected eval result: {type(value).__name__}")
    if "error" in data:
        raise DouyinCommentError(data["error"])
    return data


def js_fetch_main(aweme_id: str, cursor: int, count: int, fallback_base_url: str = "") -> str:
    return f"""
(async () => {{
  const observed = performance.getEntriesByType('resource')
    .map(e => e.name)
    .reverse()
    .find(url => url.includes('/aweme/v1/web/comment/list/'));
  const base = observed || {json.dumps(fallback_base_url)};
  if (!base) return JSON.stringify({{error: 'No observed Douyin comment request is available.'}});
  const awemeId = {json.dumps(aweme_id)};
  const u = new URL(base);
  u.pathname = '/aweme/v1/web/comment/list/';
  u.searchParams.set('aweme_id', awemeId);
  u.searchParams.set('cursor', String({cursor}));
  u.searchParams.set('count', String({count}));
  u.searchParams.set('item_type', '0');
  u.searchParams.set('cut_version', '1');
  const r = await fetch(u.toString(), {{credentials: 'include'}});
  const text = await r.text();
  let data;
  try {{ data = JSON.parse(text); }}
  catch (e) {{ return JSON.stringify({{error: 'Main comments returned non-JSON: ' + text.slice(0, 200), http_status: r.status}}); }}
  return JSON.stringify({{http_status: r.status, data}});
}})()
"""


def js_fetch_replies(
    aweme_id: str,
    comment_id: str,
    cursor: int,
    count: int,
    fallback_base_url: str = "",
) -> str:
    return f"""
(async () => {{
  const observed = performance.getEntriesByType('resource')
    .map(e => e.name)
    .reverse()
    .find(url => url.includes('/aweme/v1/web/comment/list/'));
  const base = observed || {json.dumps(fallback_base_url)};
  if (!base) return JSON.stringify({{error: 'No observed Douyin comment request is available.'}});
  const awemeId = {json.dumps(aweme_id)};
  const commentId = {json.dumps(comment_id)};
  const u = new URL(base);
  u.pathname = '/aweme/v2/web/comment/list/reply/';
  for (const k of ['aweme_id','a_bogus','x-secsdk-web-signature','timestamp','pc_img_format','rcFT']) {{
    u.searchParams.delete(k);
  }}
  u.searchParams.set('item_id', awemeId);
  u.searchParams.set('comment_id', commentId);
  u.searchParams.set('cursor', String({cursor}));
  u.searchParams.set('count', String({count}));
  u.searchParams.set('item_type', '0');
  u.searchParams.set('cut_version', '1');
  if (!u.searchParams.has('whale_cut_token')) u.searchParams.set('whale_cut_token', '');
  const r = await fetch(u.toString(), {{credentials: 'include'}});
  const text = await r.text();
  let data;
  try {{ data = JSON.parse(text); }}
  catch (e) {{ return JSON.stringify({{error: 'Replies returned non-JSON: ' + text.slice(0, 200), http_status: r.status}}); }}
  return JSON.stringify({{http_status: r.status, data}});
}})()
"""


def fetch_main_comments(
    target: str,
    base_url: str,
    aweme_id: str,
    count: int,
    page_limit: int,
    delay: float,
    progress_every: int = 50,
    progress_enabled: bool = True,
    started_at: float | None = None,
    max_main_comments: int | None = 100,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()
    started_at = started_at or time.monotonic()
    cursor = 0
    for _ in range(page_limit):
        got = fetch_page(target, js_fetch_main(aweme_id, cursor, count, base_url))
        data = got.get("data") or {}
        page_comments = data.get("comments") or []
        page = {
            "kind": "main",
            "request_cursor": cursor,
            "http_status": got.get("http_status"),
            "status_code": data.get("status_code"),
            "returned_cursor": data.get("cursor"),
            "has_more": data.get("has_more"),
            "total": data.get("total"),
            "count": len(page_comments),
        }
        pages.append(page)
        capped = False
        for comment in page_comments:
            if max_main_comments and len(comments) >= max_main_comments:
                capped = True
                break
            cid = str(comment.get("cid") or "")
            if cid and cid not in seen:
                seen.add(cid)
                comments.append(comment)
                if max_main_comments and len(comments) >= max_main_comments:
                    capped = True
                    break
        if capped and data.get("has_more"):
            page["capped_by_max_main_comments"] = True
            page["max_main_comments"] = max_main_comments
        next_cursor = data.get("cursor")
        if capped:
            page["termination_reason"] = "max_main_comments"
            break
        if not page_comments:
            if data.get("has_more"):
                page["cursor_stalled"] = True
                page["termination_reason"] = "main_empty_page_with_more"
            else:
                page["termination_reason"] = "main_exhausted"
            break
        if not data.get("has_more"):
            page["termination_reason"] = "main_exhausted"
            break
        if next_cursor in (None, cursor):
            page["cursor_stalled"] = True
            page["termination_reason"] = "main_cursor_stalled"
            break
        cursor = int(next_cursor)
        if progress_every and len(pages) % progress_every == 0:
            progress(
                progress_enabled,
                "main-progress",
                pages=len(pages),
                main_comments=len(comments),
                reported=page.get("total"),
                cursor=cursor,
                elapsed_seconds=round(time.monotonic() - started_at, 1),
            )
        time.sleep(delay)
    if pages and pages[-1].get("has_more") and not pages[-1].get("termination_reason"):
        pages[-1]["truncated_by_page_limit"] = True
        pages[-1]["termination_reason"] = "main_page_limit"
    return comments, pages


def fetch_replies(
    target: str,
    base_url: str,
    aweme_id: str,
    comments: list[dict[str, Any]],
    count: int,
    page_limit: int,
    delay: float,
    progress_every: int = 50,
    progress_enabled: bool = True,
    started_at: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reply_rows: list[dict[str, Any]] = []
    reply_pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    started_at = started_at or time.monotonic()
    parents = [comment for comment in comments if reply_total(comment) and str(comment.get("cid") or "")]
    expected = sum(reply_total(comment) for comment in parents)
    progress(
        progress_enabled,
        "reply-start",
        parents_with_replies=len(parents),
        expected_replies_from_main=expected,
        elapsed_seconds=round(time.monotonic() - started_at, 1),
    )
    for index, comment in enumerate(parents, start=1):
        total = reply_total(comment)
        parent_cid = str(comment.get("cid") or "")
        cursor = 0
        max_pages = min(page_limit, max(1, (total + count - 1) // count + 2))
        for _ in range(max_pages):
            try:
                got = fetch_page(target, js_fetch_replies(aweme_id, parent_cid, cursor, count, base_url))
            except DouyinCommentError as exc:
                reply_pages.append(
                    {
                        "kind": "reply",
                        "parent_cid": parent_cid,
                        "request_cursor": cursor,
                        "expected": total,
                        "error": str(exc),
                        "termination_reason": "reply_error",
                    }
                )
                break
            data = got.get("data") or {}
            replies = data.get("comments") or []
            reply_pages.append(
                {
                    "kind": "reply",
                    "parent_cid": parent_cid,
                    "request_cursor": cursor,
                    "http_status": got.get("http_status"),
                    "status_code": data.get("status_code"),
                    "returned_cursor": data.get("cursor"),
                    "has_more": data.get("has_more"),
                    "total": data.get("total"),
                    "count": len(replies),
                }
            )
            for reply in replies:
                cid = str(reply.get("cid") or "")
                if cid and cid not in seen:
                    seen.add(cid)
                    reply_rows.append({"parent_cid": parent_cid, "raw": reply})
            next_cursor = data.get("cursor")
            if not replies:
                if data.get("has_more"):
                    reply_pages[-1]["cursor_stalled"] = True
                    reply_pages[-1]["termination_reason"] = "reply_empty_page_with_more"
                else:
                    reply_pages[-1]["termination_reason"] = "reply_exhausted"
                break
            if not data.get("has_more"):
                reply_pages[-1]["termination_reason"] = "reply_exhausted"
                break
            if next_cursor in (None, cursor):
                reply_pages[-1]["cursor_stalled"] = True
                reply_pages[-1]["termination_reason"] = "reply_cursor_stalled"
                break
            cursor = int(next_cursor)
            time.sleep(delay)
        else:
            if (
                reply_pages
                and reply_pages[-1].get("parent_cid") == parent_cid
                and reply_pages[-1].get("has_more")
            ):
                reply_pages[-1]["truncated_by_page_limit"] = True
                reply_pages[-1]["termination_reason"] = "reply_page_limit"
        if progress_every and (index % progress_every == 0 or index == len(parents)):
            progress(
                progress_enabled,
                "reply-progress",
                parents_done=index,
                parents_total=len(parents),
                reply_rows=len(reply_rows),
                reply_pages=len(reply_pages),
                elapsed_seconds=round(time.monotonic() - started_at, 1),
            )
        time.sleep(delay)
    return reply_rows, reply_pages


def iso_time(ts: Any) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def china_time(ts: Any) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts) + 8 * 3600, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_comment(c: dict[str, Any], level: str, aweme_id: str, parent_cid: str = "") -> dict[str, Any]:
    if is_normalized_row(c):
        row = {field: c.get(field, "") for field in CSV_FIELDS}
        row["level"] = row.get("level") or level
        row["parent_cid"] = parent_cid or row.get("parent_cid", "")
        row["aweme_id"] = row.get("aweme_id") or aweme_id
        return row
    user = c.get("user") or {}
    sec_uid = user.get("sec_uid") or user.get("secUid") or ""
    ts = c.get("create_time") or c.get("createTime")
    return {
        "row_index": 0,
        "level": level,
        "parent_cid": parent_cid,
        "cid": c.get("cid") or "",
        "aweme_id": c.get("aweme_id") or aweme_id,
        "create_time": ts or "",
        "create_time_iso": iso_time(ts),
        "create_time_cn": china_time(ts),
        "ip_label": c.get("ip_label") or c.get("ipLabel") or "",
        "nickname": user.get("nickname") or "",
        "uid": user.get("uid") or "",
        "sec_uid": sec_uid,
        "user_url": f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
        "digg_count": c.get("digg_count", c.get("diggCount", "")),
        "reply_comment_total": c.get("reply_comment_total", c.get("replyTotal", 0)),
        "reply_to_reply_id": c.get("reply_to_reply_id") or c.get("replyToReplyId") or "",
        "reply_to_userid": c.get("reply_to_userid") or c.get("reply_to_user_id") or c.get("replyToUserId") or "",
        "reply_to_username": c.get("reply_to_username") or c.get("replyToUserName") or "",
        "label_text": c.get("label_text") or c.get("labelText") or "",
        "is_hot": c.get("is_hot", c.get("isHot", "")),
        "status": c.get("status", ""),
        "text": c.get("text") or "",
    }


def neutralize_csv_formula(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" \t\r\n")
    if candidate.startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: neutralize_csv_formula(row.get(field, "")) for field in CSV_FIELDS}


def write_outputs(
    out_dir: Path,
    basename: str,
    source_url: str,
    aweme_id: str,
    main_comments: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    reply_pages: list[dict[str, Any]],
    output_kind: str = "full",
    full_requested: bool = False,
    sample_main_comment_limit: int | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [normalize_comment(c, "main", aweme_id) for c in main_comments]
    rows.extend(normalize_comment(r["raw"], "reply", aweme_id, r["parent_cid"]) for r in replies)
    for idx, row in enumerate(rows, start=1):
        row["row_index"] = idx

    total_reported = next((p.get("total") for p in pages if p.get("total") is not None), None)
    expected_replies = sum(reply_total(c) for c in main_comments)
    parents_with_replies = sum(1 for c in main_comments if reply_total(c))
    capped = main_comments_capped(pages)
    main_incomplete = main_comments_incomplete(pages)
    includes_replies = not output_kind.startswith("main_only")
    reply_incomplete = includes_replies and (
        replies_incomplete(reply_pages) or (expected_replies > len(replies))
    )
    complete = not capped and not main_incomplete and not reply_incomplete
    is_sample = output_kind in {"sample", "main_only_sample"} or not complete
    output_kind = output_label(no_replies=not includes_replies, is_sample=is_sample)
    termination_reasons = [
        str(page.get("termination_reason"))
        for page in [*pages, *reply_pages]
        if page.get("termination_reason")
    ]
    if capped:
        termination_reasons.append("max_main_comments")
    if reply_incomplete and expected_replies > len(replies):
        termination_reasons.append("reply_coverage_gap")
    coverage = {
        "total_reported": total_reported,
        "visible_rows": len(rows),
        "main_comment_count": len(main_comments),
        "reply_count": len(replies),
        "parents_with_replies": parents_with_replies,
        "expected_replies_from_main": expected_replies,
        "reply_fetch_completion_ratio": round(len(replies) / expected_replies, 4) if expected_replies else None,
        "reported_gap": total_reported - len(rows) if isinstance(total_reported, int) else None,
        "is_sample": is_sample,
        "sample_main_comment_limit": sample_main_comment_limit,
        "main_comments_capped": capped,
        "full_requested": full_requested,
        "complete": complete,
        "main_fetch_incomplete": main_incomplete,
        "reply_fetch_incomplete": reply_incomplete,
        "termination_reasons": sorted(set(termination_reasons)),
    }
    if is_sample:
        coverage["sampling_note"] = (
            "This is a bounded comment sample, not the full comment section. "
            "Run again with --full for exhaustive visible main comments and replies."
        )
    payload = {
        "source_url": source_url,
        "aweme_id": aweme_id,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "output_kind": output_kind,
        "is_sample": is_sample,
        "notes": (
            "Fetched through the logged-in browser context. Main comments use "
            "/aweme/v1/web/comment/list/; replies use /aweme/v2/web/comment/list/reply/. "
            "Counts may differ from UI if Douyin folds, deletes, or hides comments."
        ),
        "total_reported": total_reported,
        "main_comment_count": len(main_comments),
        "reply_count": len(replies),
        "row_count": len(rows),
        "coverage": coverage,
        "pages": pages,
        "reply_pages": reply_pages,
        "rows": rows,
    }

    json_path = out_dir / f"{basename}_{output_kind}.json"
    csv_path = out_dir / f"{basename}_{output_kind}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_safe_row(row) for row in rows)
    return json_path, csv_path, payload


def read_main_rows_from_json(path: Path) -> tuple[str | None, str | None, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        raise DouyinCommentError(f"resume JSON has no rows list: {path}")
    main_rows = [dict(row) for row in rows if isinstance(row, dict) and (row.get("level") in {"", "main", None})]
    if not main_rows:
        raise DouyinCommentError(f"resume JSON has no main comment rows: {path}")
    pages = data.get("pages") or []
    return data.get("source_url"), str(data.get("aweme_id") or "") or None, main_rows, pages, data


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Douyin comments through a local browser bridge.")
    parser.add_argument("url", help="Douyin note/video URL")
    parser.add_argument("--target", help="Existing local-browser-bridge target id to reuse")
    parser.add_argument(
        "--browser-endpoint",
        help="Loopback browser bridge origin. Defaults to DY_NOTE_BROWSER_ENDPOINT or http://127.0.0.1:3456.",
    )
    parser.add_argument("--keep-tab", action="store_true", help="Do not close the tab opened by this script")
    parser.add_argument("--out-dir", default=".", help="Output directory")
    parser.add_argument("--basename", help="Output basename without _full.json/_full.csv")
    parser.add_argument("--resume-from-json", type=Path, help="Reuse a previous main-only/full JSON and only fetch replies.")
    parser.add_argument("--full", action="store_true", help="Fetch all visible main comments within --main-page-limit instead of the default bounded sample.")
    parser.add_argument(
        "--max-main-comments",
        type=int,
        default=100,
        help="Default sample cap for main comments. Use --full or 0 to disable.",
    )
    parser.add_argument("--no-replies", action="store_true", help="Only fetch top-level comments")
    parser.add_argument("--main-count", type=int, default=50, help="Main comment page size")
    parser.add_argument("--reply-count", type=int, default=50, help="Reply page size")
    parser.add_argument("--main-page-limit", type=int, default=300, help="Maximum main comment pages")
    parser.add_argument("--reply-page-limit", type=int, default=10, help="Maximum reply pages per thread")
    parser.add_argument("--wait", type=float, default=3.0, help="Seconds to wait after opening a new tab")
    parser.add_argument("--delay", type=float, default=0.25, help="Delay between page requests")
    parser.add_argument("--main-delay", type=float, help="Delay between main comment pages. Defaults to --delay.")
    parser.add_argument("--reply-delay", type=float, help="Delay between reply requests/parents. Defaults to --delay.")
    parser.add_argument("--progress-every", type=int, default=50, help="Print progress every N main pages or reply parents.")
    parser.add_argument("--quiet-progress", action="store_true", help="Suppress JSON progress lines on stderr.")
    parser.add_argument(
        "--no-main-checkpoint",
        action="store_true",
        help="Do not write <basename>_main_only_<sample|full>.json/csv before fetching replies.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        configure_browser_bridge(args.browser_endpoint)
    except DouyinCommentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    started_at = time.monotonic()
    progress_enabled = not args.quiet_progress
    main_delay = args.main_delay if args.main_delay is not None else args.delay
    reply_delay = args.reply_delay if args.reply_delay is not None else args.delay
    max_main_comments = None if args.full or args.max_main_comments <= 0 else args.max_main_comments
    aweme_id = extract_aweme_id(args.url)
    basename = args.basename or f"douyin_comments_{aweme_id}"
    target = args.target
    opened_by_script = False
    try:
        source_url = args.url
        pages: list[dict[str, Any]]
        resume_is_sample = False
        if args.resume_from_json:
            resume_source_url, resume_aweme_id, main_comments, pages, resume_payload = read_main_rows_from_json(args.resume_from_json)
            resume_is_sample = bool(resume_payload.get("is_sample") or resume_payload.get("coverage", {}).get("is_sample"))
            if resume_aweme_id:
                aweme_id = resume_aweme_id
            if resume_source_url:
                source_url = resume_source_url
            progress(
                progress_enabled,
                "resume-main",
                json=str(args.resume_from_json),
                main_comments=len(main_comments),
                is_sample=resume_is_sample,
                elapsed_seconds=round(time.monotonic() - started_at, 1),
            )
        else:
            main_comments = []
            pages = []

        needs_browser = not args.resume_from_json or not args.no_replies
        if needs_browser and not target:
            target = open_target(args.url)
            opened_by_script = True
            time.sleep(args.wait)
        base_url = find_comment_base_url(target) if needs_browser and target else ""

        if not args.resume_from_json:
            main_comments, pages = fetch_main_comments(
                target,
                base_url,
                aweme_id,
                args.main_count,
                args.main_page_limit,
                main_delay,
                args.progress_every,
                progress_enabled,
                started_at,
                max_main_comments,
            )
            progress(
                progress_enabled,
                "main-complete",
                main_comments=len(main_comments),
                pages=len(pages),
                reported=next((p.get("total") for p in pages if p.get("total") is not None), None),
                is_sample=main_comments_capped(pages),
                elapsed_seconds=round(time.monotonic() - started_at, 1),
            )

        replies: list[dict[str, Any]] = []
        reply_pages: list[dict[str, Any]] = []
        if not args.no_replies and not args.resume_from_json and not args.no_main_checkpoint:
            checkpoint_kind = output_label(no_replies=True, is_sample=main_comments_capped(pages))
            checkpoint_json, checkpoint_csv, _ = write_outputs(
                Path(args.out_dir),
                basename,
                source_url,
                aweme_id,
                main_comments,
                [],
                pages,
                [],
                checkpoint_kind,
                args.full,
                max_main_comments,
            )
            progress(
                progress_enabled,
                "main-checkpoint",
                json=str(checkpoint_json),
                csv=str(checkpoint_csv),
                elapsed_seconds=round(time.monotonic() - started_at, 1),
            )
        if not args.no_replies:
            replies, reply_pages = fetch_replies(
                target,
                base_url,
                aweme_id,
                main_comments,
                args.reply_count,
                args.reply_page_limit,
                reply_delay,
                args.progress_every,
                progress_enabled,
                started_at,
            )
        final_is_sample = main_comments_capped(pages)
        if args.resume_from_json:
            final_is_sample = final_is_sample or resume_is_sample
        final_kind = output_label(no_replies=args.no_replies, is_sample=final_is_sample)
        json_path, csv_path, payload = write_outputs(
            Path(args.out_dir),
            basename,
            source_url,
            aweme_id,
            main_comments,
            replies,
            pages,
            reply_pages,
            final_kind,
            args.full,
            max_main_comments,
        )
        print(json.dumps(
            {
                "json": str(json_path),
                "csv": str(csv_path),
                "main": payload["main_comment_count"],
                "replies": payload["reply_count"],
                "rows": payload["row_count"],
                "reported": payload["total_reported"],
                "output_kind": payload["output_kind"],
                "is_sample": payload["is_sample"],
                "coverage": payload["coverage"],
                "elapsed_seconds": round(time.monotonic() - started_at, 1),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    except DouyinCommentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if target and opened_by_script and not args.keep_tab:
            close_target(target)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
