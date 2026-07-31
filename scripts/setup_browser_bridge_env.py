#!/usr/bin/env python3
"""Install the small, pinned dependency set for DyNote's browser bridge."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import local_browser_bridge

PROXY_ENV_NAMES = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def pip_environment(no_proxy: bool) -> dict[str, str]:
    environment = os.environ.copy()
    if no_proxy:
        for name in PROXY_ENV_NAMES:
            environment.pop(name, None)
    return environment


def install(
    venv_dir: Path,
    requirements: Path,
    *,
    python: str,
    no_proxy: bool,
    dry_run: bool,
) -> Path:
    target_python = venv_python(venv_dir)
    if dry_run:
        print(f"create venv: {venv_dir}")
        print(f"install pinned requirements: {requirements}")
        return target_python

    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    if not target_python.exists():
        subprocess.run([python, "-m", "venv", str(venv_dir)], check=True)

    command = [
        str(target_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--require-hashes",
        "--requirement",
        str(requirements),
    ]
    if no_proxy:
        command.insert(4, "--proxy=")
    subprocess.run(command, check=True, env=pip_environment(no_proxy))
    subprocess.run(
        [str(target_python), "-c", "import websocket; print(websocket.__version__)"],
        check=True,
        env=pip_environment(no_proxy),
    )
    return target_python


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Set up DyNote's local browser bridge.")
    parser.add_argument(
        "--venv",
        type=Path,
        default=local_browser_bridge.default_venv_dir(),
        help="Destination virtual environment.",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=root / "requirements-browser.txt",
        help="Pinned requirements file.",
    )
    parser.add_argument("--python", default=sys.executable, help="Python used to create the venv.")
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Ignore proxy environment variables while installing.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.requirements.is_file():
        raise SystemExit(f"Requirements file not found: {args.requirements}")
    target = install(
        args.venv.expanduser(),
        args.requirements.resolve(),
        python=args.python,
        no_proxy=args.no_proxy,
        dry_run=args.dry_run,
    )
    print(f"Browser bridge environment ready: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
