from __future__ import annotations

from datetime import date, datetime

import pytest

from shared import memory_files as mem
from shared.domain import ToolError, WeekOverride, normalize_name, parse_friday
from shared.store import DataStore
from tests.sample_data import CHECKLIST, TASKS, WEEK1, WEEK2


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data" / "api")
    s.reset()
    return s


@pytest.fixture
def seeded(store) -> DataStore:
    mem.update_profile(store, city="Jerusalem", ready_buffer_min=10, friday_start="12:00",
                       thursday_night_start="20:00", thursday_night_end="00:00")
    mem.upsert_tasks(store, TASKS)
    mem.set_checklist(store, CHECKLIST)
    return store


def test_store_json_round_trip_is_deterministic(store):
    store.write_json("a.json", {"b": 1, "a": [1, 2]})
    first = store.path("a.json").read_bytes()
    store.write_json("a.json", {"a": [1, 2], "b": 1})
    assert store.path("a.json").read_bytes() == first
    assert store.read_json("a.json") == {"a": [1, 2], "b": 1}
    assert store.read_json("missing.json", "default") == "default"


def test_store_jsonl_only_grows(store):
    store.append_jsonl("log.jsonl", {"n": 1})
    before = store.path("log.jsonl").read_bytes()
    store.append_jsonl("log.jsonl", {"n": 2})
    assert store.path("log.jsonl").read_bytes().startswith(before)
    assert store.read_jsonl("log.jsonl") == [{"n": 1}, {"n": 2}]


def test_store_refuses_paths_outside_root(store):
    with pytest.raises(ValueError):
        store.path("../escape.json")


def test_store_reset_wipes_and_guards(tmp_path, store):
    store.write_json("x.json", {})
    store.reset()
    assert not store.exists("x.json")
    with pytest.raises(ValueError):
        DataStore(tmp_path / "not_a_data_dir").reset()


def test_normalize_name():
    assert normalize_name(" Set Table ") == "set_table"
    assert normalize_name("challah-bake") == "challah_bake"
    with pytest.raises(ToolError):
        normalize_name("challah!")


def test_parse_friday_rejects_other_days():
    assert parse_friday(WEEK1) == date(2026, 10, 30)
    with pytest.raises(ToolError, match="Thursday"):
        parse_friday("2026-10-29")


@pytest.mark.parametrize("bad, message", [
    ({"name": "x", "minutes": 0}, "minutes"),
    ({"name": "x", "minutes": "10"}, "minutes"),
    ({"name": "x", "minutes": 10, "kind": "slow"}, "kind"),
    ({"name": "x", "minutes": 10, "priority": "maybe"}, "priority"),
    ({"name": "x", "minutes": 10, "heavy": "yes"}, "heavy"),
    ({"name": "x", "minutes": 10, "anchor": {"type": "end_by", "time": "25:00"}}, "anchor.time"),
    ({"name": "x", "minutes": 10, "kind": "passive", "anchor": {"type": "last"}}, "passive"),
    ({"name": "x", "minutes": 10, "depends_on": ["x"]}, "itself"),
    ({"name": "x", "minutes": 10, "colour": "red"}, "unknown field"),
])
def test_task_validation_errors(store, bad, message):
    with pytest.raises(ToolError, match=message):
        mem.upsert_tasks(store, [bad])


def test_profile_first_save_needs_city_and_buffer(store):
    with pytest.raises(ToolError, match="city"):
        mem.update_profile(store, friday_start="12:00")
    assert mem.load_profile(store) is None


def test_profile_saved_and_merged(seeded):
    p = mem.load_profile(seeded)
    assert (p.city, p.geonameid, p.ready_buffer_min) == ("Jerusalem", 281184, 10)
    mem.update_profile(seeded, ready_buffer_min=15)
    p = mem.load_profile(seeded)
    assert (p.city, p.ready_buffer_min, p.friday_start) == ("Jerusalem", 15, "12:00")
    assert seeded.read_json("profile.json")["thursday_night"] == {"start": "20:00", "end": "00:00"}


def test_profile_city_spelling_and_unsupported(store):
    assert mem.update_profile(store, city="jerusalem", ready_buffer_min=10).city == "Jerusalem"
    with pytest.raises(ToolError, match="not supported"):
        mem.update_profile(store, city="Haifa")


def test_upsert_tasks_round_trip(seeded):
    tasks = {t.name: t for t in mem.load_tasks(seeded)}
    assert len(tasks) == 10
    assert tasks["challah_rise"].kind == "passive"
    assert tasks["set_table"].anchor.time == "15:00"
    assert tasks["shower"].anchor.type == "last"
    assert tasks["cooking"].tags == ("dinner",)
    added, updated = mem.upsert_tasks(seeded, [{**TASKS[0], "minutes": 55}])
    assert (added, updated) == ([], ["shopping"])
    assert {t.name: t for t in mem.load_tasks(seeded)}["shopping"].minutes == 55


def test_upsert_is_all_or_nothing(seeded):
    before = seeded.path("tasks.json").read_bytes()
    with pytest.raises(ToolError, match="unknown task"):
        mem.upsert_tasks(seeded, [{"name": "dessert", "minutes": 20, "depends_on": ["shoping"]}])
    assert seeded.path("tasks.json").read_bytes() == before


def test_cycle_and_second_last_rejected(seeded):
    with pytest.raises(ToolError, match="cycle"):
        mem.upsert_tasks(seeded, [{**TASKS[0], "depends_on": ["cooking"]}])
    with pytest.raises(ToolError, match="only one task"):
        mem.upsert_tasks(seeded, [{"name": "candles_prep", "minutes": 5,
                                   "anchor": {"type": "last"}}])


def test_remove_task_is_permanent_and_strips_dependencies(seeded):
    changed = mem.remove_task(seeded, "Challah Rise")
    names = {t.name for t in mem.load_tasks(seeded)}
    assert "challah_rise" not in names and len(names) == 9
    assert changed == ["challah_bake"]
    bake = {t.name: t for t in mem.load_tasks(seeded)}["challah_bake"]
    assert bake.depends_on == ()


def test_remove_unknown_task_lists_names(seeded):
    with pytest.raises(ToolError, match="no task named dessert; tasks: challah"):
        mem.remove_task(seeded, "dessert")


def test_checklist_replace_append_and_update(seeded):
    assert [i.text for i in mem.load_checklist(seeded)][0] == "Turn off the phone"
    items = mem.set_checklist(seeded, [{"text": "Close the windows", "minutes": 1}], mode="append")
    assert len(items) == 5 and items[-1].text == "Close the windows"
    items = mem.set_checklist(seeded, [{"text": "set the ALARM", "minutes": 1}], mode="append")
    assert len(items) == 5 and items[1].minutes == 1


def test_remove_checklist_item(seeded):
    removed = mem.remove_checklist_item(seeded, "set the alarm")
    assert removed.text == "Set the alarm"
    assert [i.text for i in mem.load_checklist(seeded)] == [
        "Turn off the phone", "Turn on the hot plate", "Set the light timers"]
    with pytest.raises(ToolError, match="no checklist item"):
        mem.remove_checklist_item(seeded, "Feed the cat")


def test_log_actuals_scenario_turn5(seeded):
    changes = mem.log_actuals(seeded, WEEK1, [{"task": "challah", "minutes": 90},
                                             {"task": "cleaning", "minutes": 40}])
    assert changes == [("challah", 60, 90), ("cleaning", 50, 40)]
    assert mem.learned_minutes(seeded) == {"challah": 90, "cleaning": 40}
    assert {t.name: t for t in mem.load_tasks(seeded)}["challah"].minutes == 60


def test_learned_is_rounded_mean_of_last_three(seeded):
    for friday, minutes in [("2026-10-09", 60), ("2026-10-16", 70),
                            ("2026-10-23", 81), ("2026-10-30", 90)]:
        mem.log_actuals(seeded, friday, [{"task": "challah", "minutes": minutes}])
    assert mem.learned_minutes(seeded)["challah"] == 80  # (70+81+90)/3 = 80.33
    mem.log_actuals(seeded, "2026-10-30", [{"task": "challah", "minutes": 91}])
    assert mem.learned_minutes(seeded)["challah"] == 81  # same Friday replaced: 80.67


def test_log_actuals_unknown_task(seeded):
    with pytest.raises(ToolError, match="no task named"):
        mem.log_actuals(seeded, WEEK1, [{"task": "dessert", "minutes": 20}])


def test_scenario_turn7_overrides_stay_in_their_week(seeded):
    templates_before = seeded.path("tasks.json").read_bytes()
    mem.add_week_override(seeded, WEEK2, {"type": "move_to_thursday", "tasks": ["cleaning"]},
                          note="approved")
    mem.add_week_override(seeded, WEEK2, {"type": "skip_tags", "tags": ["dinner"]},
                          note="guests Friday night")
    assert mem.list_weeks(seeded) == [WEEK2]
    week = seeded.read_json(f"weeks/{WEEK2}.json")
    assert [o["type"] for o in week["overrides"]] == ["move_to_thursday", "skip_tags"]
    assert seeded.path("tasks.json").read_bytes() == templates_before


def test_week_skip_never_removes_a_template(seeded):
    before = seeded.path("tasks.json").read_bytes()
    mem.add_week_override(seeded, WEEK2, {"type": "skip_task", "task": "challah"})
    mem.add_week_override(seeded, WEEK2, {"type": "skip_tags", "tags": ["dinner"]})
    assert seeded.path("tasks.json").read_bytes() == before
    assert "challah" in {t.name for t in mem.load_tasks(seeded)}
    assert mem.load_week(seeded, "2026-11-13") == []


def test_identical_override_is_not_duplicated(seeded):
    change = {"type": "skip_task", "task": "flowers"}
    assert mem.add_week_override(seeded, WEEK2, change)[1] is True
    assert mem.add_week_override(seeded, WEEK2, change)[1] is False
    assert len(mem.load_week(seeded, WEEK2)) == 1


def test_override_validation(seeded):
    with pytest.raises(ToolError, match="unknown task"):
        mem.add_week_override(seeded, WEEK2, {"type": "skip_task", "task": "dessert"})
    with pytest.raises(ToolError, match="change.type"):
        mem.add_week_override(seeded, WEEK2, {"type": "cancel_shabbat"})
    with pytest.raises(ToolError, match="not a Friday"):
        mem.add_week_override(seeded, "2026-11-05", {"type": "skip_task", "task": "flowers"})
    with pytest.raises(ToolError, match="already exists"):
        mem.add_week_override(seeded, WEEK2, {"type": "add_task", "new_task": TASKS[0]})
    assert mem.list_weeks(seeded) == []


def test_add_task_override_round_trip(seeded):
    new = {"name": "guest_gift", "minutes": 10}
    override, _ = mem.add_week_override(seeded, WEEK2, {"type": "add_task", "new_task": new})
    assert mem.load_week(seeded, WEEK2) == [override]
    assert "guest_gift" not in {t.name for t in mem.load_tasks(seeded)}


def test_week_expires_after_shabbat(seeded):
    mem.add_week_override(seeded, WEEK1, {"type": "skip_task", "task": "flowers"})
    friday = date(2026, 10, 30)
    assert mem.is_week_active(friday, datetime(2026, 10, 31, 23, 59))
    assert not mem.is_week_active(friday, datetime(2026, 11, 1, 0, 0))
    assert mem.active_overrides(seeded, WEEK1, datetime(2026, 10, 29, 9, 0)) != []
    assert mem.active_overrides(seeded, WEEK1, datetime(2026, 11, 5, 9, 0)) == []
    assert mem.list_weeks(seeded) == [WEEK1]


def test_override_dataclass_round_trip():
    raw = {"type": "set_minutes", "task": "cleaning", "minutes": 30, "note": "half clean"}
    assert WeekOverride.from_dict(raw).to_dict() == raw
