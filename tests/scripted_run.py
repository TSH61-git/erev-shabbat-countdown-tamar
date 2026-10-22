from __future__ import annotations

from datetime import datetime

from shared.store import DataStore
from shared.tools_impl import ToolContext, run_tool
from tests.fakes import FakeHebcal
from tests.sample_data import CHECKLIST, TASKS, WEEK1, WEEK2

IDEAL_CALLS = {
    (1, 1, "2026-10-29T09:00"): [
        ("update_profile", {"city": "Jerusalem", "ready_buffer_min": 10, "friday_start": "12:00",
                            "thursday_night_start": "20:00", "thursday_night_end": "00:00"})],
    (1, 2, "2026-10-29T09:05"): [("upsert_tasks", {"tasks": TASKS})],
    (1, 3, "2026-10-29T09:10"): [("set_checklist", {"items": CHECKLIST})],
    (1, 4, "2026-10-29T09:15"): [("get_shabbat_times", {"friday_date": WEEK1}),
                                 ("build_timeline", {"friday_date": WEEK1})],
    (1, 5, "2026-10-31T19:00"): [("log_actuals", {"friday_date": WEEK1, "entries": [
        {"task": "challah", "minutes": 90}, {"task": "cleaning", "minutes": 40}]})],
    (2, 6, "2026-11-05T09:00"): [("get_shabbat_times", {"friday_date": WEEK2}),
                                 ("build_timeline", {"friday_date": WEEK2})],
    (2, 7, "2026-11-05T09:10"): [
        ("set_week_override", {"friday_date": WEEK2, "note": "approved",
                               "change": {"type": "move_to_thursday", "tasks": ["cleaning"]}}),
        ("set_week_override", {"friday_date": WEEK2, "note": "guests Friday night",
                               "change": {"type": "skip_tags", "tags": ["dinner"]}}),
        ("build_timeline", {"friday_date": WEEK2})],
    (2, 8, "2026-11-06T13:00"): [("build_timeline", {"friday_date": WEEK2,
                                                     "now": "2026-11-06T13:00",
                                                     "delay_minutes": 40})],
}


def run_ideal(store: DataStore) -> dict[int, list[str]]:
    fetch = FakeHebcal()
    outputs: dict[int, list[str]] = {}
    for (session, turn, now), calls in IDEAL_CALLS.items():
        ctx = ToolContext(store, datetime.fromisoformat(now), turn, session, fetch)
        outputs[turn] = [run_tool(ctx, name, args) for name, args in calls]
    return outputs
