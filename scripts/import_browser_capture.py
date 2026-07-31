#!/usr/bin/env python3
"""Import a sanitized visible-page capture produced by a browser-capable agent."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse

import archive_dy_note_assets as assets
import douyin_web_ai_brief as web_ai
import extract_douyin_text as douyin_text


SCHEMA = "dy-note-browser-capture-v1"
ALLOWED_METHODS = {"codex-chrome", "browser-skill", "manual-visible-page"}
FORBIDDEN_KEYS = {
    "abogus",
    "accesstoken",
    "authorization",
    "cookie",
    "cookies",
    "headers",
    "localstorage",
    "mstoken",
    "requestheaders",
    "signature",
    "signedurl",
    "storagestate",
    "token",
    "verifyfp",
    "xbogus",
    "xsecsdkwebsignature",
}
MEDIA_HOST_SUFFIXES = douyin_text.DOUYIN_MEDIA_HOSTS


class BrowserCaptureError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def find_forbidden_key(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if normalized_key(str(key)) in FORBIDDEN_KEYS:
                return child_path
            found = find_forbidden_key(child, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = find_forbidden_key(child, f"{path}[{index}]")
            if found:
                return found
    return None


def is_signed_media_url(value: str) -> bool:
    if not value.startswith(("http://", "https://")):
        return False
    try:
        parsed = parse.urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    is_media_host = any(host == suffix or host.endswith("." + suffix) for suffix in MEDIA_HOST_SUFFIXES)
    return is_media_host and bool(parsed.query)


def find_signed_media_url(value: Any, path: str = "$") -> str | None:
    if isinstance(value, str) and is_signed_media_url(value):
        return path
    if isinstance(value, dict):
        for key, child in value.items():
            found = find_signed_media_url(child, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = find_signed_media_url(child, f"{path}[{index}]")
            if found:
                return found
    return None


def read_capture(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserCaptureError(f"Could not read browser capture JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BrowserCaptureError("Browser capture must be a JSON object.")
    if payload.get("schema") != SCHEMA:
        raise BrowserCaptureError(f"Browser capture schema must be {SCHEMA}.")
    if payload.get("capture_method") not in ALLOWED_METHODS:
        raise BrowserCaptureError("Browser capture_method is not supported.")
    forbidden = find_forbidden_key(payload)
    if forbidden:
        raise BrowserCaptureError(f"Browser capture contains a forbidden credential/signature field at {forbidden}.")
    signed_url = find_signed_media_url(payload)
    if signed_url:
        raise BrowserCaptureError(f"Browser capture contains a signed media URL at {signed_url}.")
    return payload


def validate_source(payload: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    source = payload.get("source")
    if not isinstance(source, dict):
        raise BrowserCaptureError("Browser capture source must be an object.")
    canonical_url = str(source.get("canonical_url") or source.get("source_url") or "").strip()
    if not canonical_url:
        raise BrowserCaptureError("Browser capture source.canonical_url is required.")
    try:
        douyin_text.validate_douyin_source_url(canonical_url)
    except douyin_text.DouyinTextError as exc:
        raise BrowserCaptureError(str(exc)) from exc
    aweme_id = str(source.get("aweme_id") or douyin_text.infer_aweme_id(canonical_url) or "").strip()
    url_aweme_id = douyin_text.infer_aweme_id(canonical_url)
    if not aweme_id or (url_aweme_id and aweme_id != url_aweme_id):
        raise BrowserCaptureError("Browser capture aweme_id is missing or does not match canonical_url.")
    return source, canonical_url, aweme_id


def ensure_same_source(out_dir: Path, aweme_id: str, force: bool) -> None:
    existing = out_dir / "page_metadata.json"
    if not existing.exists():
        return
    try:
        current = json.loads(existing.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserCaptureError(f"Existing page_metadata.json is unreadable: {exc}") from exc
    current_id = str(current.get("aweme_id") or douyin_text.infer_aweme_id(str(current.get("canonical_url") or "")) or "")
    if current_id and current_id != aweme_id:
        raise BrowserCaptureError("Output directory belongs to a different Douyin work. Use a new --out-dir.")
    if not force:
        raise BrowserCaptureError("Browser capture outputs already exist. Use --force only for the same work.")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def import_capture(capture_path: Path, out_dir: Path, force: bool = False) -> dict[str, Any]:
    payload = read_capture(capture_path)
    source, canonical_url, aweme_id = validate_source(payload)
    ensure_same_source(out_dir, aweme_id, force)
    out_dir.mkdir(parents=True, exist_ok=True)

    page_metadata = {
        "schema": "dy-note-page-observation-v1",
        "source_kind": "visible-browser-capture",
        "capture_method": payload["capture_method"],
        "captured_at": payload.get("captured_at") or now_iso(),
        "share_url": source.get("share_url"),
        "canonical_url": canonical_url,
        "source_url": canonical_url,
        "aweme_id": aweme_id,
        "title": source.get("title"),
        "author": source.get("author"),
        "duration_seconds": source.get("duration_seconds"),
        "login_observed": bool(payload.get("login_observed")),
        "video_playback_observed": bool(payload.get("video_playback_observed")),
        "video_ready_state": payload.get("video_ready_state"),
        "evidence_grade": payload.get("evidence_grade") or "visible-page-observation",
        "limitations": payload.get("limitations") or [],
    }
    write_json(out_dir / "page_metadata.json", page_metadata)

    outputs: dict[str, str] = {"page_metadata": str(out_dir / "page_metadata.json")}
    ai_payload = payload.get("douyin_ai")
    if isinstance(ai_payload, dict):
        ai_report = {
            **ai_payload,
            "schema": "dy-note-douyin-web-ai-brief-v1",
            "source_url": ai_payload.get("source_url") or canonical_url,
            "normalized_url": ai_payload.get("normalized_url") or canonical_url,
            "aweme_id": ai_payload.get("aweme_id") or aweme_id,
            "generated_at": ai_payload.get("generated_at") or payload.get("captured_at") or now_iso(),
        }
        write_json(out_dir / "douyin_ai_brief.json", ai_report)
        (out_dir / "douyin_ai_brief.md").write_text(web_ai.render_markdown(ai_report), encoding="utf-8")
        outputs["douyin_ai_json"] = str(out_dir / "douyin_ai_brief.json")
        outputs["douyin_ai_markdown"] = str(out_dir / "douyin_ai_brief.md")

    comments_payload = payload.get("comments")
    comments_path: Path | None = None
    if isinstance(comments_payload, dict):
        rows = comments_payload.get("rows") or comments_payload.get("comments") or []
        if not isinstance(rows, list):
            raise BrowserCaptureError("Browser capture comments rows must be a list.")
        coverage = comments_payload.get("coverage") if isinstance(comments_payload.get("coverage"), dict) else {}
        comment_report = {
            "schema": "dy-note-visible-comments-v1",
            "source_url": canonical_url,
            "aweme_id": aweme_id,
            "capture_kind": comments_payload.get("capture_kind") or "visible-page-sample",
            "fetched_at": comments_payload.get("captured_at") or payload.get("captured_at") or now_iso(),
            "output_kind": "sample",
            "is_sample": True,
            "row_count": len(rows),
            "coverage": {
                **coverage,
                "complete": False,
                "is_sample": True,
            },
            "rows": rows,
        }
        comments_path = out_dir / f"douyin_comments_{aweme_id}_sample.json"
        write_json(comments_path, comment_report)
        outputs["comments_json"] = str(comments_path)

    manifest = assets.build_asset_package(out_dir, comments_json=comments_path)
    outputs["asset_manifest"] = str(out_dir / "assets" / "asset_manifest.json")
    return {
        "schema": "dy-note-browser-import-report-v1",
        "status": "ok",
        "capture_method": payload["capture_method"],
        "aweme_id": aweme_id,
        "source_url": canonical_url,
        "outputs": outputs,
        "asset_counts": {
            "metadata": len(manifest["assets"]["metadata"]),
            "ai_briefs": len(manifest["assets"]["ai_briefs"]),
            "comment_rows": manifest["assets"]["comments"]["summary"].get("row_count", 0),
        },
        "limitations": page_metadata["limitations"],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a sanitized DyNote browser capture JSON.")
    parser.add_argument("capture_json", type=Path, help="JSON using the dy-note-browser-capture-v1 schema.")
    parser.add_argument("--out-dir", required=True, type=Path, help="DyNote output directory.")
    parser.add_argument("--force", action="store_true", help="Replace capture outputs only when the work id matches.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        report = import_capture(args.capture_json, args.out_dir, args.force)
    except BrowserCaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
