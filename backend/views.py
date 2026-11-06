from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from backend import hebrew
from SDK.agent import SESSION_FILE
from shared import config, hebcal, memory_files as mem, scheduler
from shared.context_line import this_friday
from shared.domain import Profile, ToolError, fmt_hhmm, parse_hhmm
from shared.scheduler import label
from shared.store import DataStore
from shared.tools_impl import LAST_TIMELINE, describe_override

Clock = Callable[[], datetime]
ZONE = ZoneInfo("Asia/Jerusalem")
DONE_DIR = "done"


def jerusalem_now() -> datetime:
    # shared/ compares naive local times (as scenario.json does), and Hebcal times are Jerusalem's.
    return datetime.now(ZONE).replace(tzinfo=None, second=0, microsecond=0)


def _at(friday: date, hhmm: str) -> datetime:
    return datetime.combine(friday, datetime.min.time()) + timedelta(minutes=parse_hhmm(hhmm))


def status(now: datetime, friday: date, day_start: str, ready: str, candles: str) -> dict:
    ready_at = _at(friday, ready)
    if now < ready_at:
        state = "before" if now < _at(friday, day_start) else "during"
    elif now < _at(friday, candles):
        state = "ready"
    else:
        state = "shabbat"
    return {"now": now.isoformat(timespec="minutes"), "state": state,
            "minutes_until_ready": int((ready_at - now).total_seconds() // 60)}


def health(store: DataStore, now: datetime, shown_dir: str, user_name: str) -> dict:
    session = store.read_json(SESSION_FILE) or {}
    return {"ok": True, "now": now.isoformat(timespec="minutes"), "friday": this_friday(now).isoformat(),
            "data_dir": shown_dir, "session_id": session.get("session_id"),
            "user": {"name": user_name, "initial": user_name[:1]}}


def profile(store: DataStore) -> dict | None:
    p = mem.load_profile(store)
    return {**p.to_dict(), "city_he": hebrew.CITY_HE.get(p.city, p.city)} if p else None


def tasks(store: DataStore) -> dict:
    learned = mem.learned_minutes(store)
    return {"tasks": [{**t.to_dict(), "label": label(t.name), "learned_minutes": learned.get(t.name)}
                      for t in mem.load_tasks(store)]}


def checklist(store: DataStore) -> dict:
    items = mem.load_checklist(store)
    return {"items": [i.to_dict() for i in items], "total_minutes": sum(i.minutes for i in items)}


def week(store: DataStore, now: datetime) -> dict:
    friday = this_friday(now).isoformat()
    return {"friday": friday,
            "overrides": [{**o.to_dict(), "text": describe_override(o)}
                          for o in mem.active_overrides(store, friday, now)]}


def _times(store: DataStore, p: Profile, friday: date, fetch: hebcal.Fetch) -> hebcal.ShabbatTimes:
    # A cache miss calls Hebcal (free, no AI) and fills the same cache the agent's tools use.
    times, _ = hebcal.get_shabbat_times(store, p.geonameid, friday.isoformat(), fetch)
    return times


def _ready(p: Profile, candles: str) -> str:
    return fmt_hhmm(parse_hhmm(candles) - p.ready_buffer_min)


def candles(store: DataStore, now: datetime, fetch: hebcal.Fetch,
            url_fetch: hebrew.UrlFetch | None = None) -> dict:
    friday = this_friday(now)
    p = mem.load_profile(store)
    # The date and parasha don't depend on the profile, so a first visit already shows them.
    geonameid = p.geonameid if p else config.CITIES["Jerusalem"]
    names = hebrew.for_friday(store, friday, geonameid, fetch, url_fetch)
    he = {"date_he": names.get("date_he"), "parasha_he": names.get("parasha_he")}
    if p is None:
        return {"available": False, "friday": friday.isoformat(), "reason": "no profile saved yet", **he}
    try:
        t = _times(store, p, friday, fetch)
    except ToolError as e:
        return {"available": False, "friday": friday.isoformat(), "reason": str(e), **he}
    ready = _ready(p, t.candles)
    return {"available": True, "friday": friday.isoformat(), "city": p.city, "candles": t.candles,
            "havdalah": t.havdalah, "parasha": t.parasha, "ready": ready,
            "ready_buffer_min": p.ready_buffer_min, "day_start": p.friday_start, **he,
            **status(now, friday, p.friday_start, ready, t.candles)}


def _done_file(friday: date) -> str:
    # One file per Friday: next week starts with nothing ticked, with no clean-up step.
    return f"{DONE_DIR}/{friday.isoformat()}.json"


def done_keys(store: DataStore, friday: date) -> list[str]:
    return store.read_json(_done_file(friday), {}).get("done", [])


def set_done(store: DataStore, now: datetime, key: str, done: bool) -> dict:
    friday = this_friday(now)
    keys = set(done_keys(store, friday))
    (keys.add if done else keys.discard)(key)
    store.write_json(_done_file(friday), {"friday": friday.isoformat(), "done": sorted(keys)})
    return {"friday": friday.isoformat(), "done": sorted(keys)}


def timeline(store: DataStore, now: datetime, fetch: hebcal.Fetch) -> dict:
    friday = this_friday(now)
    p, templates = mem.load_profile(store), mem.load_tasks(store)
    missing = [name for name, ok in (("profile", p), ("tasks", templates)) if not ok]
    if missing:
        return {"state": "empty", "friday": friday.isoformat(), "missing": missing}
    items = mem.load_checklist(store)
    saved = store.read_json(LAST_TIMELINE)
    # The agent's own plan wins for its Friday: it carries live re-plans ("40 behind")
    # that a fresh preview from the templates would silently undo.
    if saved and saved.get("friday") == friday.isoformat():
        plan = {k: v for k, v in saved.items() if k not in ("turn", "session")}
        source = "agent"
    else:
        try:
            t = _times(store, p, friday, fetch)
        except ToolError as e:
            return {"state": "unavailable", "friday": friday.isoformat(), "reason": str(e)}
        # Calls the scheduler directly, not the build_timeline tool, which would overwrite
        # the agent's last_timeline.json and write the tool log.
        plan = scheduler.build_timeline(
            friday=friday, candles=t.candles, profile=p, templates=templates,
            learned=mem.learned_minutes(store), checklist=items,
            overrides=mem.active_overrides(store, friday.isoformat(), now)).to_dict()
        source = "preview"
    return {**plan, "source": source, "checklist": [i.to_dict() for i in items],
            "done": done_keys(store, friday),
            **status(now, friday, plan["day_start"], plan["ready"], plan["candles"])}
