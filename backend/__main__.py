from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

import uvicorn

from backend.app import DEFAULT_PORT, create_app, default_data_dir
from shared import config
from shared.net import use_os_trust_store

HOST = "127.0.0.1"  # never configurable: the server acts with the user's Claude Code login


def log(msg: str) -> None:
    print(f"[backend] {msg}", flush=True)


def open_when_ready(server: uvicorn.Server, url: str) -> None:
    def wait() -> None:
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            webbrowser.open(url)
    threading.Thread(target=wait, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend",
                                     description="Erev Shabbat Countdown, local web app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args(argv)
    if os.environ.get("ANTHROPIC_API_KEY"):
        log("WARNING: ANTHROPIC_API_KEY is set, so Claude Code would bill the key, not your login.")
        log("  PowerShell:  Remove-Item Env:ANTHROPIC_API_KEY   (or open a new terminal)")
        return 2
    if os.environ.pop("CLAUDE_CODE_SSE_PORT", None):
        # Set by an editor's terminal; the Claude Code subprocess inherits our environment
        # and would try to attach to that editor instead of running headless.
        log("note: ignoring CLAUDE_CODE_SSE_PORT from this terminal")
    use_os_trust_store()
    url = f"http://{HOST}:{args.port}"
    log(f"data in {config.shown(default_data_dir())}; open {url}  (Ctrl+C to stop)")
    # No reload and no workers: on Windows uvicorn then switches to the selector event loop,
    # which cannot start the Claude Code subprocess.
    server = uvicorn.Server(uvicorn.Config(create_app(port=args.port), host=HOST, port=args.port,
                                           log_level="warning"))
    if not args.no_browser:
        open_when_ready(server, url)
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
