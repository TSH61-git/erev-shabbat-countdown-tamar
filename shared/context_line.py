from __future__ import annotations

from datetime import date, datetime, timedelta

from shared import memory_files as mem
from shared.domain import ChecklistItem, Profile, Task, WeekOverride
from shared.store import DataStore
from shared.tools_impl import describe_override


def this_friday(now: datetime) -> date:
    today = now.date()
    # On Saturday the user is reviewing the Friday that just passed.
    if today.weekday() == 5:
        return today - timedelta(days=1)
    return today + timedelta(days=(4 - today.weekday()) % 7)


def summarize(now: datetime, profile: Profile | None, overrides: list[WeekOverride],
              tasks: list[Task], learned: dict[str, int], checklist: list[ChecklistItem]) -> str:
    parts = [f"now: {now:%a %Y-%m-%d %H:%M}", f"this Friday: {this_friday(now)}"]
    if profile:
        parts += [f"city: {profile.city}", f"ready buffer: {profile.ready_buffer_min} min",
                  f"Friday start {profile.friday_start}",
                  f"Thursday night {profile.thursday_night_start}-{profile.thursday_night_end}"]
    else:
        parts.append("profile: not set")
    parts.append("week overrides: " + ("; ".join(describe_override(o) for o in overrides) or "none"))
    parts.append(f"tasks: {len(tasks)} ({sum(t.name in learned for t in tasks)} learned)")
    parts.append(f"checklist: {len(checklist)} items")
    return "<context>" + " | ".join(parts) + "</context>"


def render_context(store: DataStore, now: datetime) -> str:
    return summarize(now, mem.load_profile(store),
                     mem.active_overrides(store, this_friday(now).isoformat(), now),
                     mem.load_tasks(store), mem.learned_minutes(store), mem.load_checklist(store))
