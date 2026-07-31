#!/usr/bin/env python3
"""Safe client for DyNote's optional local browser bridge.

The bridge is deliberately limited to loopback HTTP endpoints. Codex Chrome
does not expose this endpoint; Codex workflows should create a sanitized
browser-capture JSON and import it with ``import_browser_capture.py`` instead.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib import error, parse, request


DEFAULT_ENDPOINT = "http://127.0.0.1:3456"
ENDPOINT_ENV = "DY_NOTE_BROWSER_ENDPOINT"
TOKEN_ENV = "DY_NOTE_BROWSER_TOKEN"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
TOKEN_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class BrowserBridgeError(RuntimeError):
    pass


def validate_endpoint(value: str) -> str:
    endpoint = (value or "").strip().rstrip("/")
    try:
        parsed = parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise BrowserBridgeError("Browser endpoint is invalid.") from exc
    if (
        parsed.scheme.lower() != "http"
        or (parsed.hostname or "").lower() not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise BrowserBridgeError(
            "Browser endpoint must be a loopback HTTP origin such as "
            "http://127.0.0.1:3456."
        )
    return endpoint


def resolve_endpoint(cli_value: str | None = None) -> str:
    return validate_endpoint(cli_value or os.environ.get(ENDPOINT_ENV) or DEFAULT_ENDPOINT)


def shared_cache_dir() -> str:
    return os.path.expanduser(
        os.environ.get(
            "RIMAGINATION_NOTE_CACHE",
            os.path.join("~", ".cache", "rimagination-notes"),
        )
    )


def default_token_file() -> str:
    return os.path.join(shared_cache_dir(), "browser-bridge", "token")


def validate_token(value: str) -> str:
    token = (value or "").strip()
    if not TOKEN_RE.fullmatch(token):
        raise BrowserBridgeError("Browser bridge token must be a 64-character hexadecimal value.")
    return token


def resolve_token(explicit: str | None = None, token_file: str | None = None) -> str | None:
    candidate = explicit or os.environ.get(TOKEN_ENV)
    if candidate:
        return validate_token(candidate)
    path = Path(token_file or default_token_file()).expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BrowserBridgeError(f"Could not read browser bridge token file: {exc}") from exc
    return validate_token(value)


class BrowserBridge:
    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
        token_file: str | None = None,
    ) -> None:
        self.endpoint = resolve_endpoint(endpoint)
        self.token = resolve_token(token, token_file)
        self._opener = request.build_opener(request.ProxyHandler({}))

    def json(self, method: str, path: str, body: str | None = None, timeout: int = 30) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise BrowserBridgeError("Browser bridge path must be relative to the local endpoint.")
        data = body.encode("utf-8") if body is not None else None
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        if self.token:
            headers["X-DyNote-Bridge-Token"] = self.token
        req = request.Request(
            f"{self.endpoint}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            # The endpoint is validated as a fixed loopback HTTP origin.
            with self._opener.open(req, timeout=timeout) as resp:  # nosec B310
                payload = resp.read().decode("utf-8", errors="replace")
        except (error.URLError, TimeoutError) as exc:
            raise BrowserBridgeError(f"Local browser bridge request failed: {exc}") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BrowserBridgeError(f"Local browser bridge returned non-JSON: {payload[:300]}") from exc

    def evaluate(self, target: str, javascript: str, timeout: int = 30) -> Any:
        result = self.json(
            "POST",
            f"/eval?target={parse.quote(target)}",
            javascript,
            timeout=timeout,
        )
        if "error" in result:
            raise BrowserBridgeError(f"Browser evaluation failed: {result['error']}")
        if "exceptionDetails" in result:
            details = result.get("exceptionDetails") or {}
            raise BrowserBridgeError(f"Browser evaluation exception: {details.get('text', details)}")
        return result.get("value")

    def open_target(self, url: str, timeout: int = 60) -> str:
        result = self.json("GET", f"/new?url={parse.quote(url, safe='')}", timeout=timeout)
        target = result.get("targetId")
        if not target:
            raise BrowserBridgeError(f"Could not create browser target: {result}")
        return str(target)

    def close_target(self, target: str) -> None:
        try:
            self.json("GET", f"/close?target={parse.quote(target)}", timeout=10)
        except BrowserBridgeError:
            pass

    def ready(self, timeout: int = 3) -> bool:
        try:
            result = self.json("GET", "/targets", timeout=timeout)
        except BrowserBridgeError:
            return False
        return isinstance(result, (list, dict))
