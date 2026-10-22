from __future__ import annotations

import pytest

from API.harness import caching
from API.harness.history import HISTORY, History
from API.harness.tokens import TokenMeter
from API.harness.tools import api_tool_defs
from shared.store import DataStore
from shared.tool_specs import TOOL_SPECS
from tests.fakes import message, text, thinking


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data" / "api")
    s.reset()
    return s


def user(t: str) -> dict:
    return {"role": "user", "content": [text(t)]}



def test_history_file_only_grows(store):
    h = History(store)
    h.start_session(1)
    snapshots = []
    for i in range(3):
        h.append_message(user(f"m{i}"))
        snapshots.append(store.path(HISTORY).read_bytes())
    assert all(b.startswith(a) for a, b in zip(snapshots, snapshots[1:]))


def test_new_instance_reloads_the_same_conversation(store):
    h = History(store)
    h.start_session(1)
    h.append_message(user("hi"))
    h.append_message({"role": "assistant", "content": message([thinking(), text("hello")]).content})
    again = History(store)
    again.start_session(2)
    assert again.load_active() == h.load_active()
    assert again.load_active()[1]["content"][0] == thinking()
    assert again.seq == 4


def test_compaction_marker_starts_a_new_active_conversation(store):
    h = History(store)
    for i in range(4):
        h.append_message(user(f"old {i}"))
    h.append_compaction("profile Jerusalem, 10 tasks saved", 1, 4)
    h.append_message(user("new turn"))
    active = h.load_active()
    assert len(active) == 1
    assert active[0]["content"][0]["text"] == "<summary>\nprofile Jerusalem, 10 tasks saved\n</summary>"
    assert active[0]["content"][1]["text"] == "new turn"
    assert len(store.read_jsonl(HISTORY)) == 6



def test_breakpoints_on_system_and_last_block_only():
    request = {"system": [{"type": "text", "text": "S"}], "tools": api_tool_defs(),
               "messages": [user("a"), {"role": "assistant", "content": [text("b")]}, user("c")]}
    out = caching.apply(request)
    marked = [b for m in out["messages"] for b in m["content"] if "cache_control" in b]
    assert out["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert marked == [out["messages"][-1]["content"][-1]]
    assert sum("cache_control" in t for t in out["tools"]) == 0
    assert "cache_control" not in request["system"][-1]
    assert "cache_control" not in request["messages"][-1]["content"][-1]


def test_string_content_is_converted_before_marking():
    out = caching.apply({"messages": [{"role": "user", "content": "plain"}]})
    assert out["messages"][0]["content"] == [{"type": "text", "text": "plain",
                                              "cache_control": {"type": "ephemeral"}}]



def test_cost_per_component():
    m = TokenMeter("claude-opus-5-5")
    call = m.record(1, message([], input_tokens=1_000_000, output_tokens=0).usage)
    assert call.cost == 4.0
    call = m.record(1, message([], input_tokens=0, output_tokens=1_000_000).usage)
    assert call.cost == 20.0
    call = m.record(1, message([], input_tokens=0, output_tokens=0, cache_read=1_000_000, cache_write=1_000_000).usage)
    assert call.cost == 5.2


def test_turn_summary_and_null_cache_fields():
    m = TokenMeter("claude-opus-5-5")
    m.record(2, message([], input_tokens=500, output_tokens=40, cache_read=None, cache_write=None).usage)
    m.record(2, message([], input_tokens=100, output_tokens=60, cache_read=2000, cache_write=300).usage)
    m.record(3, message([], input_tokens=1, output_tokens=1).usage)
    s = m.turn_summary(2)
    assert (s["input"], s["cache_read"], s["cache_write"], s["output"], s["model_calls"]) == \
        (600, 2000, 300, 100, 2)
    assert m.last_prompt_tokens == 1



def test_api_tool_defs_are_a_fresh_copy():
    defs = api_tool_defs()
    defs[0]["name"] = "changed"
    assert TOOL_SPECS[0]["name"] == "get_shabbat_times"
    assert [d["name"] for d in api_tool_defs()] == [s["name"] for s in TOOL_SPECS]
