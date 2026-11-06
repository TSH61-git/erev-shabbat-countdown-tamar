from __future__ import annotations

from datetime import datetime

import pytest

from backend import views
from shared import memory_files as mem
from shared.store import DataStore
from shared.tools_impl import LAST_TIMELINE, TOOL_LOG
from tests.fakes import FakeHebcal
from tests.sample_data import CHECKLIST, TASKS, WEEK1, WEEK2

FRI_10 = datetime(2026, 11, 6, 10, 0)


@pytest.fixture
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "data" / "app")


@pytest.fixture
def filled(store) -> DataStore:
    mem.update_profile(store, city="Jerusalem", ready_buffer_min=10)
    mem.upsert_tasks(store, TASKS)
    mem.set_checklist(store, CHECKLIST)
    mem.log_actuals(store, WEEK1, [{"task": "challah", "minutes": 90}])
    return store


def test_empty_store_gives_empty_views_not_errors(store):
    assert views.profile(store) is None
    assert views.tasks(store) == {"tasks": []}
    assert views.checklist(store) == {"items": [], "total_minutes": 0}
    assert views.week(store, FRI_10) == {"friday": WEEK2, "overrides": []}
    assert views.candles(store, FRI_10, FakeHebcal()) == {
        "available": False, "friday": WEEK2, "reason": "no profile saved yet"}
    assert views.timeline(store, FRI_10, FakeHebcal()) == {
        "state": "empty", "friday": WEEK2, "missing": ["profile", "tasks"]}


def test_saved_files_are_shown_as_saved(filled):
    assert views.profile(filled)["city"] == "Jerusalem"
    by_name = {t["name"]: t for t in views.tasks(filled)["tasks"]}
    assert by_name["challah"]["minutes"] == 60 and by_name["challah"]["learned_minutes"] == 90
    assert by_name["set_table"]["label"] == "Set table" and by_name["shower"]["learned_minutes"] is None
    cl = views.checklist(filled)
    assert [i["text"] for i in cl["items"]] == [i["text"] for i in CHECKLIST] and cl["total_minutes"] == 10


def test_candles_for_this_friday_and_time_left(filled):
    fetch = FakeHebcal()
    c = views.candles(filled, FRI_10, fetch)
    assert (c["candles"], c["ready"], c["parasha"]) == ("16:05", "15:55", "Chayei Sara")
    assert (c["state"], c["minutes_until_ready"]) == ("during", 355)
    views.candles(filled, FRI_10, fetch)
    assert len(fetch.calls) == 1, "second read must come from the Hebcal cache"


@pytest.mark.parametrize("now, state, left", [
    (datetime(2026, 11, 5, 20, 0), "before", 1195),   # Thursday night
    (datetime(2026, 11, 6, 7, 30), "before", 505),    # Friday, before the 08:00 start
    (datetime(2026, 11, 6, 15, 58), "ready", -3),     # between ready and candles
    (datetime(2026, 11, 7, 10, 0), "shabbat", -1085),  # Saturday still shows the Friday just passed
])
def test_status_through_the_week(filled, now, state, left):
    c = views.candles(filled, now, FakeHebcal())
    assert c["friday"] == WEEK2 and (c["state"], c["minutes_until_ready"]) == (state, left)


def test_timeline_preview_is_computed_without_writing_agent_files(filled):
    tl = views.timeline(filled, FRI_10, FakeHebcal())
    assert tl["source"] == "preview" and tl["friday"] == WEEK2 and tl["fits"] is True
    assert (tl["candles"], tl["ready"], tl["minutes_until_ready"]) == ("16:05", "15:55", 355)
    last = tl["blocks"][-1]
    assert last["kind"] == "checklist" and last["end"] == "15:55"
    assert tl["planned_minutes"]["challah"] == 90, "learned minutes are used"
    assert [i["text"] for i in tl["checklist"]] == [i["text"] for i in CHECKLIST]
    assert not filled.exists(LAST_TIMELINE) and not filled.exists(TOOL_LOG)


def test_timeline_prefers_the_agents_plan_for_this_friday(filled):
    agent_plan = views.timeline(filled, FRI_10, FakeHebcal())
    agent_plan = {k: agent_plan[k] for k in ("friday", "candles", "ready", "day_start", "blocks")}
    filled.write_json(LAST_TIMELINE, {**agent_plan, "replan_from": "13:00", "delay": 40,
                                      "turn": 8, "session": 2})
    tl = views.timeline(filled, FRI_10, FakeHebcal())
    assert tl["source"] == "agent" and tl["replan_from"] == "13:00" and tl["delay"] == 40
    assert "turn" not in tl and "session" not in tl


def test_agents_plan_for_an_old_friday_is_ignored(filled):
    filled.write_json(LAST_TIMELINE, {"friday": WEEK1, "candles": "16:11", "ready": "16:01",
                                      "day_start": "08:00", "blocks": []})
    assert views.timeline(filled, FRI_10, FakeHebcal())["source"] == "preview"


def test_week_overrides_apply_to_the_preview(filled):
    mem.add_week_override(filled, WEEK2, {"type": "skip_task", "task": "flowers"}, "guests")
    w = views.week(filled, FRI_10)
    assert w["overrides"] == [{"type": "skip_task", "task": "flowers", "note": "guests",
                               "text": "skip flowers"}]
    tl = views.timeline(filled, FRI_10, FakeHebcal())
    assert "flowers" not in {b["task"] for b in tl["blocks"]}


def test_hebcal_failure_is_reported_not_raised(filled):
    def down(params):
        raise ValueError("bad json")
    assert views.candles(filled, FRI_10, down)["available"] is False
    tl = views.timeline(filled, FRI_10, down)
    assert tl["state"] == "unavailable" and "Hebcal unavailable" in tl["reason"]


def test_clock_is_jerusalem_local_and_naive():
    now = views.jerusalem_now()
    assert now.tzinfo is None and now.second == 0
