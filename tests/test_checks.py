from __future__ import annotations

import pytest

from shared import memory_files as mem
from shared.checks import run_checks
from shared.config import PROJECT_ROOT
from shared.scenario import load_scenario
from shared.store import DataStore
from tests.scripted_run import run_ideal

EXPECT = load_scenario(PROJECT_ROOT / "scenario.json").expect


@pytest.fixture
def good(tmp_path) -> DataStore:
    store = DataStore(tmp_path / "data" / "api")
    store.reset()
    run_ideal(store)
    return store


def failed(store: DataStore) -> list[int]:
    return [c.id for c in run_checks(store, EXPECT) if not c.passed]


def rewrite_jsonl(store: DataStore, rel: str, change) -> None:
    rows = [change(r) for r in store.read_jsonl(rel)]
    store.delete(rel)
    for r in rows:
        if r is not None:
            store.append_jsonl(rel, r)


def test_ideal_run_passes_all_seven(good):
    results = run_checks(good, EXPECT)
    assert [c.id for c in results] == [1, 2, 3, 4, 5, 6, 7]
    assert all(c.passed for c in results), [c for c in results if not c.passed]


def test_empty_state_fails_every_check_without_crashing(tmp_path):
    store = DataStore(tmp_path / "data" / "api")
    store.reset()
    assert failed(store) == [1, 2, 3, 4, 5, 6, 7]


def test_1_wrong_buffer(good):
    mem.update_profile(good, ready_buffer_min=15)
    assert 1 in failed(good)


def test_2_checklist_item_missing(good):
    mem.remove_checklist_item(good, "Set the alarm")
    assert failed(good) == [2]


def test_2_checklist_not_last_block(good):
    tl = good.read_json("last_timeline.json")
    tl["blocks"] = tl["blocks"][:-1]
    good.write_json("last_timeline.json", tl)
    assert 2 in failed(good)


def test_3_learned_value_wrong(good):
    good.write_json("learned.json", {"challah": {"minutes": 85, "samples": []}})
    assert 3 in failed(good)


def test_4_session2_used_template_minutes(good):
    def back_to_60(r):
        if r["session"] == 2 and "challah" in r["planned_minutes"]:
            r["planned_minutes"]["challah"] = 60
        return r
    rewrite_jsonl(good, "timelines.jsonl", back_to_60)
    assert failed(good) == [4]


def test_5_override_leaked_to_another_week(good):
    mem.add_week_override(good, "2026-11-13", {"type": "skip_task", "task": "flowers"})
    assert failed(good) == [5]


def test_5_template_deleted_instead_of_skipped(good):
    mem.remove_task(good, "slow_cook")
    assert 5 in failed(good)


def test_5_approval_missing(good):
    week = good.read_json("weeks/2026-11-06.json")
    week["overrides"] = [o for o in week["overrides"] if o["type"] != "move_to_thursday"]
    good.write_json("weeks/2026-11-06.json", week)
    assert failed(good) == [5]


def test_6_overlap_and_late_end(good):
    tl = good.read_json("last_timeline.json")
    shopping = next(b for b in tl["blocks"] if b["task"] == "shopping")
    shopping["start"], shopping["end"] = "13:00", "16:00"
    good.write_json("last_timeline.json", tl)
    assert 6 in failed(good)


def test_6_dependency_order(good):
    tl = good.read_json("last_timeline.json")
    bake = next(b for b in tl["blocks"] if b["task"] == "challah_bake")
    bake["start"] = "13:30"
    good.write_json("last_timeline.json", tl)
    assert failed(good) == [6]


def test_7_hebcal_never_called_by_the_model(good):
    rewrite_jsonl(good, "tool_log.jsonl", lambda r: None if r["tool"] == "get_shabbat_times" else r)
    assert failed(good) == [7]
