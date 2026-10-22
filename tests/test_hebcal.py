from __future__ import annotations

import os
from datetime import date

import pytest
import requests

from shared import hebcal
from shared.domain import ToolError
from shared.store import DataStore
from tests.fakes import FakeHebcal, hebcal_fixture as fixture

JERUSALEM = 281184


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data" / "api")
    s.reset()
    return s


def test_parse_week1():
    t = hebcal.parse(fixture("2026-10-30"), date(2026, 10, 30))
    assert (t.candles, t.havdalah, t.parasha) == ("16:11", "17:28", "Vayera")


def test_parse_week2_with_extra_items():
    t = hebcal.parse(fixture("2026-11-06"), date(2026, 11, 6))
    assert (t.candles, t.havdalah, t.parasha) == ("16:05", "17:23", "Chayei Sara")


def test_request_params_use_jerusalem_without_b():
    p = hebcal.request_params(JERUSALEM, date(2026, 11, 6))
    assert p["geonameid"] == JERUSALEM and "b" not in p
    assert (p["gy"], p["gm"], p["gd"], p["cfg"]) == (2026, 11, 6, "json")


def test_second_call_comes_from_cache(store):
    fake = FakeHebcal()
    first, cached1 = hebcal.get_shabbat_times(store, JERUSALEM, "2026-10-30", fetch=fake)
    second, cached2 = hebcal.get_shabbat_times(store, JERUSALEM, "2026-10-30", fetch=fake)
    assert (cached1, cached2) == (False, True) and first == second
    assert len(fake.calls) == 1
    assert store.read_json("times_cache.json")["2026-10-30"]["candles"] == "16:11"


def test_cache_is_per_friday(store):
    fake = FakeHebcal()
    hebcal.get_shabbat_times(store, JERUSALEM, "2026-10-30", fetch=fake)
    t, cached = hebcal.get_shabbat_times(store, JERUSALEM, "2026-11-06", fetch=fake)
    assert t.candles == "16:05" and not cached and len(fake.calls) == 2


def test_network_error_becomes_tool_error(store):
    def down(params):
        raise requests.ConnectionError("no route")

    with pytest.raises(ToolError, match="Hebcal unavailable"):
        hebcal.get_shabbat_times(store, JERUSALEM, "2026-10-30", fetch=down)
    assert store.read_json("times_cache.json") is None


def test_missing_candles_item(store):
    with pytest.raises(ToolError, match="no candle lighting"):
        hebcal.get_shabbat_times(store, JERUSALEM, "2026-10-30", fetch=lambda p: {"items": []})


def test_not_a_friday(store):
    with pytest.raises(ToolError, match="not a Friday"):
        hebcal.get_shabbat_times(store, JERUSALEM, "2026-10-31", fetch=FakeHebcal())


def test_real_network_is_blocked_offline():
    if os.environ.get("HEBCAL_LIVE") == "1":
        pytest.skip("live mode")
    with pytest.raises(RuntimeError, match="blocked"):
        hebcal.fetch_json(hebcal.request_params(JERUSALEM, date(2026, 10, 30)))


@pytest.mark.skipif(os.environ.get("HEBCAL_LIVE") != "1", reason="set HEBCAL_LIVE=1 to call Hebcal")
def test_live_hebcal(store):
    t, _ = hebcal.get_shabbat_times(store, JERUSALEM, "2026-10-30")
    assert t.candles == "16:11"
