from __future__ import annotations

from datetime import datetime

import pytest

from API.harness import compaction, context, memory
from API.harness.history import History
from API.harness.tokens import TokenMeter
from shared import memory_files as mem
from shared.store import DataStore
from tests.fakes import FakeAnthropicClient, message, text, thinking, tool_use
from tests.sample_data import CHECKLIST, TASKS


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data" / "api")
    s.reset()
    mem.update_profile(s, city="Jerusalem", ready_buffer_min=10, friday_start="12:00")
    mem.upsert_tasks(s, TASKS)
    mem.set_checklist(s, CHECKLIST)
    return s


def test_memory_loads_both_layers_and_skips_expired_weeks(store, capsys):
    mem.add_week_override(store, "2026-10-30", {"type": "skip_task", "task": "flowers"})
    mem.add_week_override(store, "2026-11-06", {"type": "skip_tags", "tags": ["dinner"]})
    view = memory.load(store, datetime(2026, 11, 5, 9, 0))
    assert view.profile.city == "Jerusalem" and len(view.tasks) == 10
    assert [o.type for o in view.overrides] == ["skip_tags"]
    assert "[memory.py] expired weeks not loaded: 2026-10-30" in capsys.readouterr().out


def test_context_message_holds_summary_not_task_list(store):
    view = memory.load(store, datetime(2026, 11, 5, 9, 0))
    msg = context.build_user_message(view, "Plan this Friday.")
    first, second = msg["content"]
    assert first["text"].startswith("<context>now: Thu 2026-11-05 09:00")
    assert "tasks: 10 (0 learned)" in first["text"]
    assert not any(t["name"] in first["text"] for t in TASKS)
    assert second == {"type": "text", "text": "Plan this Friday."}


@pytest.mark.parametrize("tokens, expected", [(9000, False), (9001, True), (0, False)])
def test_should_compact_threshold(tokens, expected):
    assert compaction.should_compact(tokens, 9000) is expected


def test_transcript_drops_thinking_keeps_tools():
    msgs = [{"role": "user", "content": [text("Build my plan")]},
            {"role": "assistant", "content": [thinking(), tool_use("t1", "list_tasks", {})]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                          "content": "shopping 50m active must"}]}]
    out = compaction.transcript(msgs)
    assert out.splitlines() == ["user: Build my plan", "assistant called list_tasks {}",
                                "tool result: shopping 50m active must"]


def test_compact_writes_marker_and_restarts_from_summary(store):
    h = History(store)
    h.append_message({"role": "user", "content": [text("old question")]})
    h.append_message({"role": "assistant", "content": [thinking(), text("old answer")]}, 9500)
    client = FakeAnthropicClient([message([text("User lives in Jerusalem.")])])
    base = {"model": "claude-opus-5-5", "max_tokens": 16000, "tools": [{"name": "x"}],
            "tool_choice": {"type": "auto"}, "system": [], "messages": []}
    summary = compaction.compact(client, base, h, TokenMeter("claude-opus-5-5"), turn=6)
    request = client.messages.requests[0]
    assert "tools" not in request and "tool_choice" not in request
    assert request["system"][0]["text"] == compaction.SUMMARY_SYSTEM
    assert "old question" in request["messages"][0]["content"]
    assert summary == "User lives in Jerusalem."
    h.append_message({"role": "user", "content": [text("new turn")]})
    active = h.load_active()
    assert len(active) == 1 and active[0]["content"][0]["text"].startswith("<summary>")
    assert h.last_prompt_tokens() == 0
    assert len(store.read_jsonl("history.jsonl")) == 4
