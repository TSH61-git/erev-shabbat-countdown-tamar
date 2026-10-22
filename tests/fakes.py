from __future__ import annotations

import json

from shared.config import PROJECT_ROOT


def hebcal_fixture(friday: str) -> dict:
    path = PROJECT_ROOT / "shared" / "fixtures" / f"hebcal_{friday}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class FakeHebcal:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, params: dict) -> dict:
        self.calls.append(params)
        return hebcal_fixture(f"{params['gy']}-{params['gm']:02d}-{params['gd']:02d}")
