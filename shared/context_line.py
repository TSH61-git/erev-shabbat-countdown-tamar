from __future__ import annotations

from datetime import date, datetime, timedelta

from shared import memory_files as mem
from shared.store import DataStore
from shared.tools_impl import describe_override


def this_friday(now: datetime) -> date:
    today = now.date()
    # On Saturday the user is reviewing the Friday that just passed.
    if today.weekday() == 5:
        return today - timedelta(days=1)
    return today + timedelta(days=(4 - today.weekday()) % 7)


def render_context(store: DataStore, now: datetime) -> str:
    friday = this_friday(now)
    parts = [f"now: {now:%a %Y-%m-%d %H:%M}", f"this Friday: {friday}"]
    profile = mem.load_profile(store)
    if profile:
        parts += [f"city: {profile.city}", f"ready buffer: {profile.ready_buffer_min} min",
                  f"Friday start {profile.friday_start}",
                  f"Thursday night {profile.thursday_night_start}-{profile.thursday_night_end}"]
    else:
        parts.append("profile: not set")
    overrides = mem.active_overrides(store, friday.isoformat(), now)
    parts.append("week overrides: " + ("; ".join(describe_override(o) for o in overrides) or "none"))
    tasks = mem.load_tasks(store)
    learned = mem.learned_minutes(store)
    parts.append(f"tasks: {len(tasks)} ({sum(t.name in learned for t in tasks)} learned)")
    parts.append(f"checklist: {len(mem.load_checklist(store))} items")
    return "<context>" + " | ".join(parts) + "</context>"
