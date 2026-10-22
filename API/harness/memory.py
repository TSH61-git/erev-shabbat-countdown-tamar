from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from shared import memory_files as mem
from shared.context_line import this_friday
from shared.domain import ChecklistItem, Profile, Task, WeekOverride
from shared.store import DataStore


def log(msg: str) -> None:
    print(f"[memory.py] {msg}", flush=True)


@dataclass(frozen=True)
class MemoryView:
    now: datetime
    friday: date
    profile: Profile | None
    tasks: list[Task]
    learned: dict[str, int]
    checklist: list[ChecklistItem]
    overrides: list[WeekOverride]


def load(store: DataStore, now: datetime) -> MemoryView:
    # Read fresh every turn: tools may have changed the files during the last turn.
    friday = this_friday(now)
    view = MemoryView(now, friday, mem.load_profile(store), mem.load_tasks(store),
                      mem.learned_minutes(store), mem.load_checklist(store),
                      mem.active_overrides(store, friday.isoformat(), now))
    p = view.profile
    log(f"permanent: profile {f'{p.city}/{p.ready_buffer_min}' if p else 'not set'}, "
        f"{len(view.tasks)} tasks, {len(view.checklist)} checklist items, "
        f"{len(view.learned)} learned")
    log(f"week {friday}: {len(view.overrides)} overrides")
    expired = [w for w in mem.list_weeks(store)
               if not mem.is_week_active(date.fromisoformat(w), now)]
    if expired:
        log(f"expired weeks not loaded: {', '.join(expired)}")
    return view
