#!/usr/bin/env python3
"""Authenticated loopback bridge for a dedicated Chrome DevTools session."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import secrets
import stat
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import browser_bridge

DEFAULT_CDP_PORT = 9223
DEFAULT_BRIDGE_PORT = 3456
MAX_JAVASCRIPT_BYTES = 1024 * 1024
MAX_SELECTOR_BYTES = 4096
MAX_REQUEST_BODY_BYTES = MAX_JAVASCRIPT_BYTES
TARGET_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ALLOWED_NAVIGATION_HOSTS = ("douyin.com", "iesdouyin.com", "doubao.com")
FORBIDDEN_JAVASCRIPT = (
    "document.cookie",
    "localstorage",
    "sessionstorage",
    "indexeddb",
    "cookiestore",
    "navigator.credentials",
)


class LocalBridgeError(RuntimeError):
    pass


def bridge_cache_dir() -> Path:
    return Path(browser_bridge.shared_cache_dir()) / "browser-bridge"


def default_profile_dir() -> Path:
    return bridge_cache_dir() / "chrome-profile"


def default_venv_dir() -> Path:
    return bridge_cache_dir() / "venv"


def default_token_path() -> Path:
    return Path(browser_bridge.default_token_file())


def validate_port(value: int, label: str) -> int:
    if not 1024 <= int(value) <= 65535:
        raise LocalBridgeError(f"{label} must be between 1024 and 65535.")
    return int(value)


def validate_target_id(value: str) -> str:
    if not TARGET_RE.fullmatch(value or ""):
        raise LocalBridgeError("Invalid Chrome target id.")
    return value


def host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(normalized == suffix or normalized.endswith("." + suffix) for suffix in suffixes)


def validate_navigation_url(value: str) -> str:
    if value == "about:blank":
        return value
    try:
        parsed = parse.urlsplit(value)
    except ValueError as exc:
        raise LocalBridgeError("Invalid browser navigation URL.") from exc
    if (
        parsed.scheme.lower() != "https"
        or not host_matches(parsed.hostname or "", ALLOWED_NAVIGATION_HOSTS)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise LocalBridgeError("Browser navigation is limited to allowlisted HTTPS sites.")
    return value


def validate_javascript(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocalBridgeError("JavaScript expression is required.")
    if len(value.encode("utf-8")) > MAX_JAVASCRIPT_BYTES:
        raise LocalBridgeError("JavaScript expression is too large.")
    compact = re.sub(r"\s+", "", value).lower()
    if any(forbidden in compact for forbidden in FORBIDDEN_JAVASCRIPT):
        raise LocalBridgeError("JavaScript expression requests forbidden browser credential storage.")
    return value


def ensure_local_token(path: Path | None = None) -> str:
    token_path = (path or default_token_path()).expanduser()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        token_path.parent.chmod(0o700)
    if token_path.exists():
        file_stat = token_path.lstat()
        if token_path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
            raise LocalBridgeError("Browser bridge token path must be a regular file.")
        try:
            token = browser_bridge.validate_token(token_path.read_text(encoding="utf-8").strip())
        except (OSError, browser_bridge.BrowserBridgeError) as exc:
            raise LocalBridgeError(f"Existing browser bridge token is invalid: {exc}") from exc
    else:
        token = secrets.token_hex(32)
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(token_path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(token + "\n")
        except FileExistsError:
            return ensure_local_token(token_path)
        except OSError as exc:
            raise LocalBridgeError(f"Could not write browser bridge token: {exc}") from exc
    if os.name != "nt":
        token_path.chmod(0o600)
    return token


def validate_websocket_url(value: str, cdp_port: int) -> str:
    try:
        parsed = parse.urlsplit(value)
    except ValueError as exc:
        raise LocalBridgeError("Invalid Chrome DevTools websocket URL.") from exc
    if (
        parsed.scheme != "ws"
        or (parsed.hostname or "").lower() not in browser_bridge.LOOPBACK_HOSTS
        or parsed.port != cdp_port
        or not parsed.path.startswith("/devtools/page/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LocalBridgeError("Chrome DevTools websocket URL escaped the local page target.")
    return value


class ChromeDevTools:
    def __init__(self, port: int = DEFAULT_CDP_PORT) -> None:
        self.port = validate_port(port, "CDP port")
        self.origin = f"http://127.0.0.1:{self.port}"
        self._opener = request.build_opener(request.ProxyHandler({}))

    def _request(self, method: str, path: str, timeout: int = 10) -> bytes:
        if not path.startswith("/") or path.startswith("//"):
            raise LocalBridgeError("Invalid Chrome DevTools path.")
        req = request.Request(f"{self.origin}{path}", method=method)
        try:
            with self._opener.open(req, timeout=timeout) as resp:  # nosec B310
                return resp.read()
        except (error.URLError, TimeoutError) as exc:
            raise LocalBridgeError(f"Chrome DevTools request failed: {exc}") from exc

    def _json(self, method: str, path: str, timeout: int = 10) -> Any:
        payload = self._request(method, path, timeout).decode("utf-8", errors="replace")
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LocalBridgeError(f"Chrome DevTools returned non-JSON: {payload[:200]}") from exc

    def ready(self) -> bool:
        try:
            payload = self._json("GET", "/json/version", timeout=2)
        except LocalBridgeError:
            return False
        return isinstance(payload, dict) and bool(payload.get("webSocketDebuggerUrl"))

    def targets(self) -> list[dict[str, Any]]:
        payload = self._json("GET", "/json/list")
        if not isinstance(payload, list):
            raise LocalBridgeError("Chrome DevTools target list is invalid.")
        return [item for item in payload if isinstance(item, dict) and item.get("type") == "page"]

    def new_target(self, url: str) -> dict[str, Any]:
        safe_url = validate_navigation_url(url)
        payload = self._json("PUT", f"/json/new?{parse.quote(safe_url, safe='')}")
        if not isinstance(payload, dict) or not payload.get("id"):
            raise LocalBridgeError("Chrome did not create a page target.")
        return payload

    def close_target(self, target_id: str) -> None:
        self._request("GET", f"/json/close/{validate_target_id(target_id)}")

    def target(self, target_id: str) -> dict[str, Any]:
        expected = validate_target_id(target_id)
        for target in self.targets():
            if target.get("id") == expected:
                return target
        raise LocalBridgeError("Chrome target was not found.")

    def evaluate(self, target_id: str, javascript: str, timeout: int = 30) -> dict[str, Any]:
        expression = validate_javascript(javascript)
        target = self.target(target_id)
        ws_url = validate_websocket_url(str(target.get("webSocketDebuggerUrl") or ""), self.port)
        try:
            import websocket
        except ImportError as exc:
            raise LocalBridgeError(
                "websocket-client is missing; run scripts/setup_browser_bridge_env.py."
            ) from exc

        command_id = secrets.randbelow(2_000_000_000) + 1
        command = {
            "id": command_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
        }
        deadline = time.monotonic() + timeout
        try:
            ws = websocket.create_connection(
                ws_url,
                timeout=timeout,
                suppress_origin=True,
                http_proxy_host=None,
                http_proxy_port=None,
            )
            try:
                ws.send(json.dumps(command, separators=(",", ":")))
                while time.monotonic() < deadline:
                    message = ws.recv()
                    if isinstance(message, bytes):
                        message = message.decode("utf-8", errors="replace")
                    payload = json.loads(message)
                    if payload.get("id") == command_id:
                        return payload
            finally:
                ws.close()
        except Exception as exc:
            raise LocalBridgeError(f"Chrome DevTools evaluation failed: {exc}") from exc
        raise LocalBridgeError("Chrome DevTools evaluation timed out.")


def normalize_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("error"):
        return {"error": payload["error"]}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if result.get("exceptionDetails"):
        return {"exceptionDetails": result["exceptionDetails"]}
    remote = result.get("result") if isinstance(result.get("result"), dict) else {}
    if "value" in remote:
        return {"value": remote["value"]}
    if "unserializableValue" in remote:
        return {"value": remote["unserializableValue"]}
    return {"value": None}


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "DyNoteBridge/0.2"
    sys_version = ""
    token = ""
    cdp: ChromeDevTools
    bridge_port = DEFAULT_BRIDGE_PORT

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _is_authorized(self) -> bool:
        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if not peer.is_loopback or self.headers.get("Origin"):
            return False
        allowed_hosts = {
            f"127.0.0.1:{self.bridge_port}",
            f"localhost:{self.bridge_port}",
            f"[::1]:{self.bridge_port}",
        }
        if self.headers.get("Host", "").lower() not in allowed_hosts:
            return False
        supplied = self.headers.get("X-DyNote-Bridge-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _query_value(self, name: str) -> str:
        query = parse.parse_qs(parse.urlsplit(self.path).query, keep_blank_values=True)
        values = query.get(name) or []
        if len(values) != 1 or not values[0]:
            raise LocalBridgeError(f"Query parameter {name} is required.")
        return values[0]

    def _read_body(self) -> str:
        if self.headers.get("Transfer-Encoding"):
            raise LocalBridgeError("Chunked request bodies are not supported.")
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise LocalBridgeError("Content-Length is required.") from exc
        if length < 0 or length > MAX_REQUEST_BODY_BYTES:
            raise LocalBridgeError("Request body is too large.")
        return self.rfile.read(length).decode("utf-8")

    def _dispatch_get(self) -> Any:
        path = parse.urlsplit(self.path).path
        if path == "/health":
            return {"status": "ok", "cdp_ready": self.cdp.ready()}
        if path == "/targets":
            return self.cdp.targets()
        if path == "/new":
            target = self.cdp.new_target(self._query_value("url"))
            return {"targetId": target["id"]}
        if path == "/close":
            self.cdp.close_target(self._query_value("target"))
            return {"closed": True}
        raise FileNotFoundError

    def _dispatch_post(self) -> Any:
        path = parse.urlsplit(self.path).path
        target = self._query_value("target")
        body = self._read_body()
        if path == "/eval":
            return normalize_evaluation(self.cdp.evaluate(target, body))
        if path == "/clickAt":
            if len(body.encode("utf-8")) > MAX_SELECTOR_BYTES:
                raise LocalBridgeError("Selector is too large.")
            selector = json.dumps(body)
            expression = (
                "(() => {"
                f"const el = document.querySelector({selector});"
                "if (!el) return {clicked:false,error:'selector not found'};"
                "el.click(); return {clicked:true};"
                "})()"
            )
            return normalize_evaluation(self.cdp.evaluate(target, expression, timeout=20))
        raise FileNotFoundError

    def do_GET(self) -> None:
        if not self._is_authorized():
            self._fail(HTTPStatus.UNAUTHORIZED, "Unauthorized.")
            return
        try:
            self._send_json(HTTPStatus.OK, self._dispatch_get())
        except FileNotFoundError:
            self._fail(HTTPStatus.NOT_FOUND, "Not found.")
        except LocalBridgeError as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        if not self._is_authorized():
            self._fail(HTTPStatus.UNAUTHORIZED, "Unauthorized.")
            return
        try:
            self._send_json(HTTPStatus.OK, self._dispatch_post())
        except FileNotFoundError:
            self._fail(HTTPStatus.NOT_FOUND, "Not found.")
        except (UnicodeDecodeError, LocalBridgeError) as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))


def make_server(
    bridge_port: int,
    token: str,
    cdp: ChromeDevTools,
    *,
    allow_ephemeral_port: bool = False,
) -> ThreadingHTTPServer:
    port = 0 if allow_ephemeral_port and bridge_port == 0 else validate_port(bridge_port, "Bridge port")
    safe_token = browser_bridge.validate_token(token)

    class ConfiguredHandler(BridgeRequestHandler):
        pass

    ConfiguredHandler.token = safe_token
    ConfiguredHandler.cdp = cdp
    ConfiguredHandler.bridge_port = port
    server = ThreadingHTTPServer(("127.0.0.1", port), ConfiguredHandler)
    if port == 0:
        ConfiguredHandler.bridge_port = int(server.server_address[1])
    server.daemon_threads = True
    server.block_on_close = False
    return server


if __name__ == "__main__":
    print("Run this module through start_browser_bridge.py.", file=sys.stderr)
    raise SystemExit(2)
