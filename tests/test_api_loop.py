from __future__ import annotations

import re
from datetime import datetime

import pytest

from API.harness import tools
from API.harness.history import History
from API.harness.loop import MAX_CALLS, run_turn
from API.harness.tokens import TokenMeter
from shared.store import DataStore
from shared.tools_impl import ToolContext, run_tool
from tests.fakes import FakeAnthropicClient, FakeHebcal, message, text, thinking, tool_use
from tests.sample_data import TASKS, WEEK1


@pytest.fixture
def env(tmp_path):
    store = DataStore(tmp_path / "data" / "api")
    store.reset()
    ctx = ToolContext(store, datetime(2026, 10, 29, 9, 15), turn=4, session=1, fetch=FakeHebcal())
    run_tool(ctx, "update_profile", {"city": "Jerusalem", "ready_buffer_min": 10,
                                     "friday_start": "12:00"})
    run_tool(ctx, "upsert_tasks", {"tasks": TASKS})
    history = History(store)
    history.append_message({"role": "user", "content": [text("Build my Friday plan.")]})
    return ctx, history


def go(env, script):
    ctx, history = env
    client = FakeAnthropicClient(script)
    meter = TokenMeter("claude-opus-5-5")
    outcome = run_turn(client, lambda msgs: {"model": "claude-opus-5-5", "messages": msgs},
                       history, lambda b: tools.dispatch(ctx, b), meter, turn=4)
    return outcome, client, history, meter


def test_end_turn_returns_text(env):
    outcome, client, history, meter = go(env, [message([text("Shabbat shalom")])])
    assert (outcome.text, outcome.model_calls, outcome.stop_reason) == ("Shabbat shalom", 1, "end_turn")
    assert [m["role"] for m in history.load_active()] == ["user", "assistant"]
    assert len(meter.calls) == 1


def test_parallel_tool_results_go_back_in_one_user_message(env):
    script = [
        message([thinking(), tool_use("tu_1", "get_shabbat_times", {"friday_date": WEEK1}),
                 tool_use("tu_2", "list_tasks", {})], stop_reason="tool_use"),
        message([text("done")]),
    ]
    outcome, client, history, _ = go(env, script)
    assert outcome.tools == ["get_shabbat_times", "list_tasks"] and outcome.model_calls == 2
    second = client.messages.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    results = second[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu_1", "tu_2"]
    assert all(r["type"] == "tool_result" and "is_error" not in r for r in results)
    assert results[0]["content"].startswith("2026-10-30 Jerusalem: candles 16:11")


def test_thinking_block_is_replayed_unchanged(env):
    block = thinking("abc123==")
    script = [message([block, tool_use("tu_1", "list_tasks", {})], stop_reason="tool_use"),
              message([text("ok")])]
    _, client, _, _ = go(env, script)
    assert client.messages.requests[1]["messages"][1]["content"][0] == block


def test_tool_error_becomes_is_error_and_loop_continues(env):
    script = [message([tool_use("tu_1", "remove_task", {"name": "dessert"}),
                       tool_use("tu_2", "no_such_tool", {})], stop_reason="tool_use"),
              message([text("there is no dessert task")])]
    outcome, client, _, _ = go(env, script)
    results = client.messages.requests[1]["messages"][-1]["content"]
    assert [r["is_error"] for r in results] == [True, True]
    assert "no task named dessert" in results[0]["content"]
    assert outcome.text == "there is no dessert task"


def test_max_calls_guard(env):
    loop_forever = [message([tool_use(f"tu_{i}", "list_tasks", {})], stop_reason="tool_use")
                    for i in range(MAX_CALLS)]
    outcome, client, history, _ = go(env, loop_forever)
    assert outcome.stop_reason == "max_calls" and len(client.messages.requests) == MAX_CALLS
    assert history.load_active()[-1]["content"][0]["type"] == "tool_result"


def test_cut_off_tool_use_still_gets_a_result(env):
    script = [message([tool_use("tu_1", "list_tasks", {})], stop_reason="max_tokens")]
    outcome, _, history, _ = go(env, script)
    assert outcome.stop_reason == "max_tokens"
    last = history.load_active()[-1]["content"][0]
    assert last["tool_use_id"] == "tu_1" and last["is_error"] is True


def test_refusal_stops_the_turn(env):
    outcome, _, _, _ = go(env, [message([], stop_reason="refusal")])
    assert outcome.stop_reason == "refusal" and outcome.text == "[refusal]"


def test_every_print_names_its_file(env, capsys):
    script = [message([tool_use("tu_1", "remove_task", {"name": "dessert"})], stop_reason="tool_use"),
              message([text("ok")])]
    go(env, script)
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines and all(re.match(r"^\[[a-z_]+\.py\] ", l) for l in lines)
    joined = "\n".join(lines)
    assert "[loop.py] turn 4 call 1: stop_reason=tool_use (1 tool_use)" in joined
    assert "[tools.py] dispatch remove_task" in joined
    assert "[tools.py] remove_task ERROR -> is_error" in joined
