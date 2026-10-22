from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import requests

from shared.domain import ToolError, parse_friday
from shared.store import DataStore

URL = "https://www.hebcal.com/shabbat"
CACHE = "times_cache.json"

Fetch = Callable[[dict], dict]


@dataclass(frozen=True)
class ShabbatTimes:
    friday: date
    candles: str
    havdalah: str | None
    parasha: str | None


def request_params(geonameid: int, friday: date) -> dict:
    # No "b": Hebcal already uses 40 minutes for Jerusalem, and sending 18 would override it.
    return {"cfg": "json", "geonameid": geonameid, "gy": friday.year, "gm": friday.month,
            "gd": friday.day, "M": "on", "leyning": "off"}


def fetch_json(params: dict) -> dict:
    resp = requests.get(URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse(data: dict, friday: date) -> ShabbatTimes:
    items = data.get("items", [])

    # Hebcal returns the whole week; an Erev Chag earlier in the week also has a
    # candles item, so match on the Friday's date, not just the category.
    def time_on(category: str, day: date) -> str | None:
        for item in items:
            stamp = item.get("date", "")
            if item.get("category") == category and stamp.startswith(day.isoformat() + "T"):
                return stamp[11:16]
        return None

    candles = time_on("candles", friday)
    if candles is None:
        raise ToolError(f"Hebcal returned no candle lighting for {friday}")
    parasha = next((i["title"].removeprefix("Parashat ") for i in items
                    if i.get("category") == "parashat"), None)
    return ShabbatTimes(friday, candles, time_on("havdalah", friday + timedelta(days=1)), parasha)


def get_shabbat_times(store: DataStore, geonameid: int, friday_date: object,
                      fetch: Fetch = fetch_json) -> tuple[ShabbatTimes, bool]:
    friday = parse_friday(friday_date)
    cache = store.read_json(CACHE, {})
    hit = cache.get(friday.isoformat())
    if hit and hit["geonameid"] == geonameid:
        return ShabbatTimes(friday, hit["candles"], hit["havdalah"], hit["parasha"]), True
    try:
        data = fetch(request_params(geonameid, friday))
    except (requests.RequestException, ValueError) as e:
        raise ToolError(f"Hebcal unavailable ({type(e).__name__}); try again later") from None
    times = parse(data, friday)
    cache[friday.isoformat()] = {"geonameid": geonameid, "candles": times.candles,
                                 "havdalah": times.havdalah, "parasha": times.parasha}
    store.write_json(CACHE, cache)
    return times, False
