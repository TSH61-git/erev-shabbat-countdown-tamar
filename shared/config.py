from __future__ import annotations

from pathlib import Path

MODEL = "claude-opus-5-5"
EFFORT = "medium"

# Hebcal applies Jerusalem's 40-minute candle-lighting offset by itself for this
# id, so we never send its "b" parameter.
CITIES = {"Jerusalem": 281184}

# City and ready buffer have no defaults on purpose: check 1 must prove the
# agent saved what the user said, not a value we filled in.
DEFAULT_FRIDAY_START = "08:00"
DEFAULT_THURSDAY_NIGHT = {"start": "20:00", "end": "00:00"}

LEARNED_WINDOW = 3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "data"


def data_dir(version: str, override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    if version not in ("api", "sdk"):
        raise ValueError(f"unknown version {version!r}")
    return DATA_ROOT / version


def canonical_city(name: str) -> str | None:
    wanted = name.strip().casefold()
    for city in CITIES:
        if city.casefold() == wanted:
            return city
    return None
