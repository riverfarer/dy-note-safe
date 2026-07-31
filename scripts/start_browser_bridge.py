#!/usr/bin/env python3
"""Launch a dedicated Chrome profile and DyNote's authenticated bridge."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import local_browser_bridge

DEFAULT_OPEN_URL = "https://www.douyin.com/"
VENV_MARKER = "DY_NOTE_BROWSER_VENV_ACTIVE"


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def ensure_runtime(venv_dir: Path) -> None:
    try:
        import websocket  # noqa: F401
    except ImportError:
        target = venv_python(venv_dir)
        if os.environ.get(VENV_MARKER) or not target.is_file():
            raise local_browser_bridge.LocalBridgeError(
                "Browser bridge dependency is missing. Run "
                "scripts/setup_browser_bridge_env.py first."
            ) from None
        environment = os.environ.copy()
        environment[VENV_MARKER] = "1"
        os.execve(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]], environment)


def chrome_candidates() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )
    elif sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.extend(
                    [
                        Path(base) / "Google/Chrome/Application/chrome.exe",
                        Path(base) / "Chromium/Application/chrome.exe",
                    ]
                )
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        executable = shutil.which(name)
        if executable:
            candidates.append(Path(executable))
    return candidates


def find_chrome(explicit: Path | None = None) -> Path:
    if explicit:
        candidate = explicit.expanduser()
        if candidate.is_file():
            return candidate
        raise local_browser_bridge.LocalBridgeError(f"Chrome executable not found: {candidate}")
    for candidate in chrome_candidates():
        if candidate.is_file():
            return candidate
    raise local_browser_bridge.LocalBridgeError(
        "Google Chrome or Chromium was not found. Pass --chrome with its executable path."
    )


def launch_chrome(chrome: Path, profile_dir: Path, cdp_port: int, open_url: str) -> subprocess.Popen[bytes]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        profile_dir.chmod(0o700)
    safe_url = local_browser_bridge.validate_navigation_url(open_url)
    command = [
        str(chrome),
        f"--user-data-dir={profile_dir}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={cdp_port}",
        "--no-first-run",
        "--no-default-browser-check",
        safe_url,
    ]
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def wait_for_cdp(cdp: local_browser_bridge.ChromeDevTools, process: subprocess.Popen[bytes] | None) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if cdp.ready():
            return
        if process is not None and process.poll() is not None:
            raise local_browser_bridge.LocalBridgeError(
                f"Chrome exited before DevTools became ready (exit {process.returncode})."
            )
        time.sleep(0.25)
    raise local_browser_bridge.LocalBridgeError("Chrome DevTools did not become ready within 20 seconds.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start DyNote's authenticated local bridge with a dedicated Chrome profile."
    )
    parser.add_argument("--bridge-port", type=int, default=local_browser_bridge.DEFAULT_BRIDGE_PORT)
    parser.add_argument("--cdp-port", type=int, default=local_browser_bridge.DEFAULT_CDP_PORT)
    parser.add_argument("--profile-dir", type=Path, default=local_browser_bridge.default_profile_dir())
    parser.add_argument("--token-file", type=Path, default=local_browser_bridge.default_token_path())
    parser.add_argument("--venv", type=Path, default=local_browser_bridge.default_venv_dir())
    parser.add_argument("--chrome", type=Path)
    parser.add_argument("--open-url", default=DEFAULT_OPEN_URL)
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Connect to an already-running dedicated Chrome DevTools instance.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    local_browser_bridge.validate_port(args.bridge_port, "Bridge port")
    local_browser_bridge.validate_port(args.cdp_port, "CDP port")
    ensure_runtime(args.venv.expanduser())

    cdp = local_browser_bridge.ChromeDevTools(args.cdp_port)
    chrome_process = None
    if not args.no_launch and not cdp.ready():
        chrome_process = launch_chrome(
            find_chrome(args.chrome),
            args.profile_dir.expanduser(),
            args.cdp_port,
            args.open_url,
        )
    wait_for_cdp(cdp, chrome_process)

    token = local_browser_bridge.ensure_local_token(args.token_file)
    server = local_browser_bridge.make_server(args.bridge_port, token, cdp)
    print(f"DyNote browser bridge: http://127.0.0.1:{args.bridge_port}", flush=True)
    print(f"Dedicated Chrome profile: {args.profile_dir.expanduser()}", flush=True)
    print("Keep this process running; press Ctrl-C to stop the bridge.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except local_browser_bridge.LocalBridgeError as exc:
        print(f"Browser bridge error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
