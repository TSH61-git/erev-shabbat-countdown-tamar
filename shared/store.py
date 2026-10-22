from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


class DataStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"path {rel!r} is outside the data directory")
        return p

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    def list_json(self, rel_dir: str) -> list[str]:
        d = self.path(rel_dir)
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.json"))

    def read_json(self, rel: str, default: Any = None) -> Any:
        p = self.path(rel)
        if not p.exists():
            return default
        with p.open(encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, rel: str, data: Any) -> None:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Sorted keys: the same state must give the same bytes, for tests and caching.
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        # Temp file + rename, so a crash mid-write never leaves half a file.
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, rel: str) -> None:
        self.path(rel).unlink(missing_ok=True)

    def append_jsonl(self, rel: str, record: Any) -> None:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        with p.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)

    def read_jsonl(self, rel: str) -> list[Any]:
        p = self.path(rel)
        if not p.exists():
            return []
        with p.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def reset(self) -> None:
        # A wrong argument must never be able to wipe the project or a home folder.
        if "data" not in self.root.parts:
            raise ValueError(f"refusing to reset {self.root}: not under a 'data' folder")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
