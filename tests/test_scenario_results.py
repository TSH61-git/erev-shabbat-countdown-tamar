from __future__ import annotations

import json

import pytest

from shared.checks import CheckResult
from shared.config import PROJECT_ROOT
from shared.results import (HARNESS_FILES, TurnRecord, api_loc, build_results, sdk_loc, totals,
                            write_results)
from shared.scenario import load_scenario, reset_state
from tests.sample_data import TASKS


def test_scenario_file_shape():
    sc = load_scenario(PROJECT_ROOT / "scenario.json")
    assert [len(s) for s in sc.sessions] == [5, 3]
    assert [t.turn for t in sc.turns] == list(range(1, 9))
    turn2 = sc.turns[1].text
    assert all(t["name"] in turn2 for t in TASKS)
    assert "move cleaning to Thursday" in sc.turns[6].text


def write_scenario(tmp_path, **turn_changes) -> str:
    data = json.loads((PROJECT_ROOT / "scenario.json").read_text(encoding="utf-8"))
    data["sessions"][0]["turns"][0].update(turn_changes)
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_scenario_rejects_wrong_friday(tmp_path):
    with pytest.raises(ValueError, match="belongs to Friday"):
        load_scenario(write_scenario(tmp_path, friday="2026-11-06"))


def test_scenario_rejects_time_going_back(tmp_path):
    with pytest.raises(ValueError, match="backwards"):
        load_scenario(write_scenario(tmp_path, now="2026-10-29T23:00"))


def test_reset_state_wipes(tmp_path):
    store = reset_state(tmp_path / "data" / "api")
    store.write_json("profile.json", {})
    assert not reset_state(tmp_path / "data" / "api").exists("profile.json")


def test_api_loc_counts_code_lines_only(tmp_path):
    (tmp_path / "loop.py").write_text("# why\n\nimport x\n\ndef f():\n    return 1\n", encoding="utf-8")
    loc = api_loc(tmp_path)
    assert loc["loop.py"] == 3 and loc["tokens.py"] == 0 and list(loc) == HARNESS_FILES


def test_sdk_loc_counts_marked_lines(tmp_path):
    (tmp_path / "agent.py").write_text(
        "opts = Options(\n"
        "    resume=sid,  # replaces: history.py\n"
        "    max_turns=15,  # replaces: loop.py\n"
        ")\n"
        "# replaces: compaction.py (automatic in Claude Code)\n", encoding="utf-8")
    loc = sdk_loc(tmp_path)
    assert loc["history.py"] == 1 and loc["loop.py"] == 1 and loc["compaction.py"] == 0


def test_totals_and_cache_percent():
    turns = [TurnRecord(1, 1, input=100, cache_read=800, cache_write=100, output=50,
                        model_calls=2, cost_usd=0.01, seconds=1.5),
             TurnRecord(2, 1, input=200, cache_read=0, cache_write=0, output=10,
                        model_calls=1, cost_usd=0.002, seconds=0.5)]
    t = totals(turns)
    assert t["tokens"] == {"input": 300, "cache_read": 800, "cache_write": 100, "cache_write_1h": 0,
                           "output": 60}
    assert t["cache_read_pct"] == 66.7 and t["model_calls"] == 3
    assert t["cost_usd"] == 0.012 and t["seconds"] == 2.0


def test_results_file_has_every_required_field(tmp_path):
    turn = TurnRecord(4, 1, input=10, output=5, model_calls=2,
                      tools=["get_shabbat_times", "build_timeline"], cost_usd=0.1, seconds=3)
    data = build_results("api", [turn], [CheckResult(7, "external tool called", True, "1")],
                         dict.fromkeys(HARNESS_FILES, 1), {"anthropic": "2.1.0"})
    path = tmp_path / "results" / "api.json"
    write_results(path, data)
    saved = json.loads(path.read_text(encoding="utf-8"))
    row = saved["turns"][0]
    assert set(row["tokens"]) == {"input", "cache_read", "cache_write", "cache_write_1h", "output"}
    assert {"model_calls", "tools", "cost_usd", "seconds"} <= set(row)
    assert saved["model"] == "claude-opus-5-5" and saved["effort"] == "medium"
    assert saved["checks_passed"] == "1/1" and saved["loc_total"] == 8
