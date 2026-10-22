from __future__ import annotations

from datetime import datetime

import pytest

from shared import memory_files as mem
from shared.context_line import render_context, this_friday
from shared.domain import ToolError
from shared.prompts import SYSTEM_PROMPT
from shared.store import DataStore
from shared.tool_specs import TOOL_NAMES, TOOL_SPECS
from shared.tools_impl import IMPLS, ToolContext, run_tool
from tests.fakes import FakeHebcal
from tests.sample_data import CHECKLIST, TASKS, WEEK2
from tests.scripted_run import run_ideal


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data" / "api")
    s.reset()
    return s


@pytest.fixture
def ctx(store) -> ToolContext:
    return ToolContext(store, datetime(2026, 11, 5, 9, 0), turn=6, session=2, fetch=FakeHebcal())


@pytest.fixture
def seeded(ctx) -> ToolContext:
    run_tool(ctx, "update_profile", {"city": "Jerusalem", "ready_buffer_min": 10,
                                     "friday_start": "12:00"})
    run_tool(ctx, "upsert_tasks", {"tasks": TASKS})
    run_tool(ctx, "set_checklist", {"items": CHECKLIST})
    return ctx


def test_specs_and_impls_match():
    assert TOOL_NAMES == list(IMPLS) and len(TOOL_NAMES) == 10
    for spec in TOOL_SPECS:
        assert spec["input_schema"]["additionalProperties"] is False
        assert len(spec["description"]) < 300


def test_system_prompt_is_static():
    assert "rav" in SYSTEM_PROMPT and "2026" not in SYSTEM_PROMPT


def test_ideal_scenario_outputs(store):
    out = run_ideal(store)
    assert out[1] == ["profile: Jerusalem, buffer 10, Fri start 12:00, Thu night 20:00-00:00"]
    assert out[2] == ["saved: added 10. 10 tasks total"]
    assert out[3] == ["checklist: 4 items, 10 min"]
    assert out[4][0] == "2026-10-30 Jerusalem: candles 16:11, havdalah 17:28 (Vayera). ready 16:01"
    assert out[5] == ["learned: challah 60->90, cleaning 50->40"]
    assert "DOES NOT FIT by 20 min" in out[6][1]
    assert out[7][1] == ("override 2026-11-06: skip tag dinner -> skips cooking(must), "
                         "slow_cook(must). CONFLICT with saved tasks. Templates unchanged; "
                         "applies to 2026-11-06 only")
    assert "dropped flowers (nice)" in out[8][0]
    for turn_outputs in out.values():
        for text in turn_outputs:
            assert len(text) < 1200 and max(len(line) for line in text.splitlines()) < 160


def test_list_tasks_is_one_line_per_task(seeded):
    mem.log_actuals(seeded.store, "2026-10-30", [{"task": "challah", "minutes": 90}])
    lines = run_tool(seeded, "list_tasks", {}).splitlines()
    assert len(lines) == 11
    assert "challah 90m* active must" in lines
    assert "set_table 15m active must by 15:00" in lines
    assert "cooking 15m active must <-shopping thu_ok #dinner" in lines
    assert lines[-1].startswith("checklist (10 min): Turn off the phone 2")


def test_remove_tools_are_permanent(seeded):
    out = run_tool(seeded, "remove_task", {"name": "challah_rise"})
    assert out == ("removed template challah_rise permanently; dependency removed from: "
                   "challah_bake. 9 tasks")
    out = run_tool(seeded, "remove_checklist_item", {"text": "set the alarm"})
    assert out == 'removed checklist item "Set the alarm" permanently. 3 items, 8 min'


def test_week_override_never_removes_a_template(seeded):
    before = seeded.store.path("tasks.json").read_bytes()
    run_tool(seeded, "set_week_override", {"friday_date": WEEK2,
                                           "change": {"type": "skip_task", "task": "challah"}})
    assert seeded.store.path("tasks.json").read_bytes() == before


def test_repeated_override_is_reported(seeded):
    args = {"friday_date": WEEK2, "change": {"type": "skip_task", "task": "flowers"}}
    run_tool(seeded, "set_week_override", args)
    assert run_tool(seeded, "set_week_override", args) == "already saved for 2026-11-06: skip flowers"


@pytest.mark.parametrize("name, args, message", [
    ("no_such_tool", {}, "unknown tool"),
    ("remove_task", {}, "missing argument"),
    ("remove_task", {"name": "flowers", "forever": True}, "unknown argument"),
    ("build_timeline", {"friday_date": WEEK2, "now": "13:00"}, "now must be"),
    ("get_shabbat_times", {"friday_date": "2026-11-07"}, "not a Friday"),
])
def test_bad_calls_raise_and_are_logged(seeded, name, args, message):
    with pytest.raises(ToolError, match=message):
        run_tool(seeded, name, args)
    last = seeded.store.read_jsonl("tool_log.jsonl")[-1]
    assert last["tool"] == name and last["ok"] is False and message in last["error"]


def test_tools_need_a_profile(ctx):
    with pytest.raises(ToolError, match="update_profile"):
        run_tool(ctx, "get_shabbat_times", {"friday_date": WEEK2})


def test_build_timeline_saves_last_and_history(seeded):
    run_tool(seeded, "build_timeline", {"friday_date": WEEK2})
    last = seeded.store.read_json("last_timeline.json")
    assert (last["turn"], last["session"], last["ready"]) == (6, 2, "15:55")
    assert len(seeded.store.read_jsonl("timelines.jsonl")) == 1


def test_delay_without_now_uses_the_turn_time(store):
    ctx = ToolContext(store, datetime(2026, 11, 6, 13, 0), 8, 2, FakeHebcal())
    run_tool(ctx, "update_profile", {"city": "Jerusalem", "ready_buffer_min": 10,
                                     "friday_start": "12:00"})
    run_tool(ctx, "upsert_tasks", {"tasks": TASKS})
    out = run_tool(ctx, "build_timeline", {"friday_date": WEEK2, "delay_minutes": 40})
    assert "RE-PLAN from 13:00 (40 behind)" in out


def test_context_line_has_only_the_summary(seeded):
    run_tool(seeded, "set_week_override", {"friday_date": WEEK2,
                                           "change": {"type": "skip_tags", "tags": ["dinner"]}})
    line = render_context(seeded.store, datetime(2026, 11, 5, 9, 0))
    assert line == ("<context>now: Thu 2026-11-05 09:00 | this Friday: 2026-11-06 | "
                    "city: Jerusalem | ready buffer: 10 min | Friday start 12:00 | "
                    "Thursday night 20:00-00:00 | week overrides: skip tag dinner | "
                    "tasks: 10 (0 learned) | checklist: 4 items</context>")
    assert "shopping" not in line


def test_context_line_before_profile(store):
    line = render_context(store, datetime(2026, 10, 29, 9, 0))
    assert "profile: not set" in line and "tasks: 0 (0 learned)" in line


@pytest.mark.parametrize("now, friday", [
    ("2026-10-29T09:00", "2026-10-30"), ("2026-10-30T13:00", "2026-10-30"),
    ("2026-10-31T19:00", "2026-10-30"), ("2026-11-01T10:00", "2026-11-06"),
])
def test_this_friday(now, friday):
    assert this_friday(datetime.fromisoformat(now)).isoformat() == friday
