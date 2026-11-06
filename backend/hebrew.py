from __future__ import annotations

from datetime import date
from typing import Callable

import requests

from shared import hebcal
from shared.store import DataStore

CONVERTER_URL = "https://www.hebcal.com/converter"
CACHE = "hebrew_cache.json"

UrlFetch = Callable[[str, dict], dict]

# Hebcal's own Hebrew month is pointed or in short spelling (בְּחֶשְׁוָן / חשון); the design uses
# the everyday full spelling.
MONTHS = {
    "Tishrei": "תשרי", "Cheshvan": "חשוון", "Kislev": "כסלו", "Tevet": "טבת", "Sh'vat": "שבט",
    "Adar": "אדר", "Adar I": "אדר א׳", "Adar II": "אדר ב׳", "Nisan": "ניסן", "Iyyar": "אייר",
    "Sivan": "סיוון", "Tamuz": "תמוז", "Av": "אב", "Elul": "אלול",
}
CITY_HE = {"Jerusalem": "ירושלים"}


def fetch_url(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def hebrew_date(conv: dict) -> str:
    parts = conv["heDateParts"]
    return f"{parts['d']} ב{MONTHS.get(conv.get('hm'), parts['m'])} {parts['y']}"


def hebrew_parasha(shabbat: dict) -> str | None:
    # Holiday Shabbatot have no weekly parasha; the card then shows only the date.
    return next((i.get("hebrew") for i in shabbat.get("items", [])
                 if i.get("category") == "parashat" and i.get("hebrew")), None)


def for_friday(store: DataStore, friday: date, geonameid: int, fetch: hebcal.Fetch,
               url_fetch: UrlFetch | None = None) -> dict:
    cache = store.read_json(CACHE, {})
    if friday.isoformat() in cache:
        return cache[friday.isoformat()]
    try:
        # Looked up at call time, so tests can replace the one network function.
        conv = (url_fetch or fetch_url)(CONVERTER_URL, {
            "cfg": "json", "gy": friday.year, "gm": friday.month, "gd": friday.day, "g2h": 1})
        shabbat = fetch(hebcal.request_params(geonameid, friday))
        found = {"date_he": hebrew_date(conv), "parasha_he": hebrew_parasha(shabbat)}
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        # Not cached: the next page refresh tries again, and the page falls back to the
        # browser's own Hebrew calendar meanwhile.
        print(f"[hebrew.py] Hebrew date unavailable for {friday}: {type(e).__name__}", flush=True)
        return {}
    cache[friday.isoformat()] = found
    store.write_json(CACHE, cache)
    return found
