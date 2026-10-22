from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path

from shared import config
from shared.checks import CheckResult

HARNESS_FILES = ["loop.py", "tools.py", "history.py", "memory.py", "context.py",
                 "compaction.py", "caching.py", "tokens.py"]
_REPLACES = re.compile(r"#\s*replaces:\s*([a-z_]+\.py)")


@dataclass
class TurnRecord:
    turn: int
    session: int
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    model_calls: int = 0
    tools: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    seconds: float = 0.0
    compacted: bool = False
    reply_preview: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tokens"] = {k: d.pop(k) for k in ("input", "cache_read", "cache_write", "output")}
        return d


def count_code_lines(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))


def api_loc(harness_dir: Path) -> dict[str, int]:
    return {f: count_code_lines(harness_dir / f) if (harness_dir / f).exists() else 0
            for f in HARNESS_FILES}


def sdk_loc(sdk_dir: Path) -> dict[str, int]:
    counts = dict.fromkeys(HARNESS_FILES, 0)
    for path in sorted(sdk_dir.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _REPLACES.search(line)
            # A line that only holds the marker documents something automatic; it is not code.
            if m and m.group(1) in counts and not line.strip().startswith("#"):
                counts[m.group(1)] += 1
    return counts


def totals(turns: list[TurnRecord]) -> dict:
    t = {k: sum(getattr(r, k) for r in turns)
         for k in ("input", "cache_read", "cache_write", "output", "model_calls")}
    prompt = t["input"] + t["cache_read"] + t["cache_write"]
    return {
        "tokens": {k: t[k] for k in ("input", "cache_read", "cache_write", "output")},
        "cache_read_pct": round(100 * t["cache_read"] / prompt, 1) if prompt else 0.0,
        "model_calls": t["model_calls"],
        "cost_usd": round(sum(r.cost_usd for r in turns), 6),
        "seconds": round(sum(r.seconds for r in turns), 2),
    }


def library_versions(names: list[str]) -> dict[str, str]:
    out = {}
    for n in names:
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            out[n] = "not installed"
    return out


def build_results(version: str, turns: list[TurnRecord], checks: list[CheckResult],
                  loc: dict[str, int], libraries: dict[str, str], extra: dict | None = None) -> dict:
    return {
        "version": version,
        "model": config.MODEL,
        "effort": config.EFFORT,
        "libraries": libraries,
        "run_finished": datetime.now().isoformat(timespec="seconds"),
        "turns": [r.to_dict() for r in turns],
        "totals": totals(turns),
        "checks": [c.to_dict() for c in checks],
        "checks_passed": f"{sum(c.passed for c in checks)}/{len(checks)}",
        "loc": loc,
        "loc_total": sum(loc.values()),
        "extra": extra or {},
    }


def write_results(path: str | Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
