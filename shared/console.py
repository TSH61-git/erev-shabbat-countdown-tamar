from __future__ import annotations

import sys
from pathlib import Path


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s: str) -> int:
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self) -> None:
        for st in self.streams:
            st.flush()


def setup_output(log_path: str | None) -> None:
    # Windows consoles default to a legacy code page; the logs contain non-ASCII text.
    sys.stdout.reconfigure(encoding="utf-8")
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = _Tee(sys.stdout, open(log_path, "w", encoding="utf-8"))
