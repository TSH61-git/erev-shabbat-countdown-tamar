import json
from pathlib import Path

import pytest
import requests

from backend import hebrew

FIXTURES = Path(__file__).parent / "fixtures"


class FakeConverter:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, url: str, params: dict) -> dict:
        self.calls.append(params)
        path = FIXTURES / f"hebcal_converter_{params['gy']}-{params['gm']:02d}-{params['gd']:02d}.json"
        if not path.exists():
            raise requests.ConnectionError(f"no fixture for {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def converter(monkeypatch) -> FakeConverter:
    # Recorded Hebcal answers instead of the network, for every backend test.
    fake = FakeConverter()
    monkeypatch.setattr(hebrew, "fetch_url", fake)
    return fake
