from __future__ import annotations

from datetime import date, datetime

import pytest

from shared import memory_files as mem
from shared.domain import ChecklistItem, Profile, Task, ToolError, WeekOverride
from shared.scheduler import build_timeline, find_violations, render
from shared.store import DataStore
from tests.sample_data import CHECKLIST, TASKS, WEEK1, WEEK2

PROFILE = Profile.build("Jerusalem", 10, "12:00", "20:00", "00:00")
TEMPLATES = [Task.from_dict(t) for t in TASKS]
ITEMS = [ChecklistItem.from_dict(c) for c in CHECKLIST]
LEARNED = {"challah": 90, "cleaning": 40}
TURN7 = [WeekOverride.from_dict({"type": "move_to_thursday", "tasks": ["cleaning"]}),
         WeekOverride.from_dict({"type": "skip_tags", "tags": ["dinner"]})]
CANDLES = {WEEK1: "16:11", WEEK2: "16:05"}


def build(friday=WEEK1, learned=None, overrides=(), templates=TEMPLATES, profile=PROFILE, **kw):
    return build_timeline(friday=date.fromisoformat(friday), candles=CANDLES[friday],
                          profile=profile, templates=templates, learned=learned or {},
                          checklist=ITEMS, overrides=list(overrides), **kw)


def spans(tl):
    return {b.task: (b.start, b.end) for b in tl.blocks if b.task}


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def test_turn4_week1_fits_with_slack_6():
    tl = build()
    assert tl.fits and tl.slack == 6 and find_violations(tl) == []
    s = spans(tl)
    assert s["shopping"] == (hm("12:00"), hm("12:50"))
    assert s["cooking"] == (hm("12:50"), hm("13:05"))
    assert s["challah"] == (hm("13:05"), hm("14:05"))
    assert s["shower"] == (hm("15:31"), hm("15:51"))
    assert tl.blocks[-1].kind == "checklist"
    assert (tl.blocks[-1].start, tl.blocks[-1].end) == (hm("15:51"), hm("16:01"))


def test_turn6_week2_over_by_20_suggests_cleaning_on_thursday():
    tl = build(WEEK2, LEARNED)
    assert tl.overflow == 20 and not tl.fits
    assert tl.suggestion.tasks == ["cleaning"]
    assert tl.suggestion.remaining_overflow == 0
    thu = tl.suggestion.blocks[0]
    assert (thu.start, thu.end) == (hm("20:00"), hm("20:40"))
    assert "cleaning" not in spans(tl)
    assert find_violations(tl) == []
    assert "DOES NOT FIT by 20 min. SUGGEST Thursday night 20:00-20:40: cleaning (40)" in render(tl)


def test_turn7_cleaning_on_thursday_and_dinner_skipped():
    tl = build(WEEK2, LEARNED, TURN7)
    assert tl.fits and find_violations(tl) == []
    assert [b.task for b in tl.thursday_blocks] == ["cleaning"]
    assert not {"cooking", "slow_cook", "cleaning"} & set(spans(tl))


def test_turn8_replan_drops_flowers_and_keeps_every_must():
    tl = build(WEEK2, LEARNED, TURN7, now=datetime(2026, 11, 6, 13, 0), delay=40)
    assert tl.fits and tl.dropped == ["flowers"] and find_violations(tl) == []
    s = spans(tl)
    assert s["challah"] == (hm("13:00"), hm("14:10"))
    assert s["set_table"] == (hm("14:10"), hm("14:25"))
    assert s["shopping"] == (hm("14:25"), hm("15:15"))
    assert s["shower"] == (hm("15:25"), hm("15:45"))
    assert s["challah_bake"][1] == hm("15:25")
    assert "RE-PLAN from 13:00 (40 behind): dropped flowers (nice)" in render(tl)


def test_learned_minutes_are_used():
    assert build(WEEK2, LEARNED).planned["challah"].minutes == 90
    assert build(WEEK1).planned["challah"].minutes == 60


def test_passive_overlaps_active_but_active_never_overlaps():
    tl = build()
    s = spans(tl)
    assert s["slow_cook"][0] < s["challah"][1]
    assert find_violations(tl) == []


def test_end_by_anchor_is_respected():
    early = [Task.from_dict({**t, "anchor": {"type": "end_by", "time": "12:30"}})
             if t["name"] == "set_table" else Task.from_dict(t) for t in TASKS]
    tl = build(templates=early)
    assert spans(tl)["set_table"][1] <= hm("12:30")
    assert find_violations(tl) == []


def test_override_for_a_removed_task_is_ignored(tmp_path):
    store = DataStore(tmp_path / "data" / "api")
    store.reset()
    mem.update_profile(store, city="Jerusalem", ready_buffer_min=10, friday_start="12:00")
    mem.upsert_tasks(store, TASKS)
    mem.set_checklist(store, CHECKLIST)
    mem.add_week_override(store, WEEK2, {"type": "skip_task", "task": "flowers"})
    mem.add_week_override(store, WEEK2, {"type": "move_to_thursday", "tasks": ["cleaning", "flowers"]})
    mem.remove_task(store, "flowers")

    tl = build_timeline(friday=date(2026, 11, 6), candles="16:05", profile=mem.load_profile(store),
                        templates=mem.load_tasks(store), learned=mem.learned_minutes(store),
                        checklist=mem.load_checklist(store), overrides=mem.load_week(store, WEEK2))
    assert tl.ignored == ["skip_task flowers (task no longer exists)",
                          "move_to_thursday flowers (task no longer exists)"]
    assert [b.task for b in tl.thursday_blocks] == ["cleaning"]
    assert find_violations(tl) == []
    assert "ignored override: skip_task flowers (task no longer exists)" in render(tl)


def test_week_skip_does_not_reach_the_next_week():
    skip = [WeekOverride.from_dict({"type": "skip_task", "task": "challah"})]
    assert "challah" not in spans(build(WEEK2, LEARNED, skip))
    assert "challah" in spans(build(WEEK1))


def test_skipping_the_dough_skips_rise_and_bake():
    skip = [WeekOverride.from_dict({"type": "skip_task", "task": "challah"})]
    assert not {"challah", "challah_rise", "challah_bake"} & set(spans(build(WEEK1, overrides=skip)))


def test_start_time_and_add_task_overrides():
    extra = [WeekOverride.from_dict({"type": "start_time", "time": "11:30"}),
             WeekOverride.from_dict({"type": "add_task", "new_task": {"name": "guest_gift", "minutes": 10}})]
    tl = build(overrides=extra)
    assert tl.day_start == hm("11:30") and "guest_gift" in spans(tl)
    assert find_violations(tl) == []


def test_must_tasks_are_reported_never_dropped():
    tl = build(WEEK2, LEARNED, TURN7, now=datetime(2026, 11, 6, 14, 0), delay=120)
    assert not tl.fits
    assert tl.dropped == ["flowers"]
    text = render(tl)
    assert "DOES NOT FIT" in text and "MUST tasks:" in text and "shower" in text


@pytest.mark.parametrize("now, delay, message", [
    (datetime(2026, 11, 5, 13, 0), 40, "Friday itself"),
    (datetime(2026, 11, 6, 16, 0), 0, "past the ready time"),
    (datetime(2026, 11, 6, 13, 0), -5, "delay_minutes"),
])
def test_replan_input_errors(now, delay, message):
    with pytest.raises(ToolError, match=message):
        build(WEEK2, LEARNED, TURN7, now=now, delay=delay)


def test_deterministic():
    assert build(WEEK2, LEARNED).to_dict() == build(WEEK2, LEARNED).to_dict()
    assert render(build()) == render(build())
