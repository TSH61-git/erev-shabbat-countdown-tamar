from __future__ import annotations

import dataclasses
import json
from datetime import datetime

import pytest
from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.chat import HISTORY, STATE, AppAgent, ChatService, TeeClient
from SDK.agent import SESSION_FILE, SdkAgent
from shared import memory_files as mem
from shared.store import DataStore
from tests.fakes import FakeClaudeCode, FakeHebcal
from tests.test_sdk import CALLS_BY_NOW

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
POST_HEADERS = {"Origin": ORIGIN}

# Times from the scenario, so FakeClaudeCode plays the matching scripted tool calls.
THU_0900 = datetime(2026, 10, 29, 9, 0)    # update_profile
THU_0905 = datetime(2026, 10, 29, 9, 5)    # upsert_tasks
THU_0910 = datetime(2026, 10, 29, 9, 10)   # set_checklist
NEXT_THU = datetime(2026, 11, 5, 9, 0)     # next week: get_shabbat_times + build_timeline


class FakeStreamingClaudeCode(FakeClaudeCode):
    # Like the real CLI with include_partial_messages: deltas first, then the full block again.
    async def receive_response(self):
        async for msg in super().receive_response():
            if isinstance(msg, AssistantMessage) and any(isinstance(b, TextBlock) for b in msg.content):
                yield StreamEvent("u1", self.session_id, {"type": "message_start",
                                                          "message": {"id": msg.message_id}})
                for piece in ("Here is ", "your plan."):
                    yield StreamEvent("u2", self.session_id, {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": piece}})
            yield msg


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(autouse=True)
def reset_fake():
    FakeClaudeCode.reset()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data" / "app"


def make_client(data_dir, clock, fake=FakeClaudeCode) -> TestClient:
    app = create_app(data_dir, clock=clock, fetch=FakeHebcal(), port=PORT,
                     client_factory=lambda o, t: fake(o, t, CALLS_BY_NOW))
    return TestClient(app, base_url=ORIGIN)


def send(client: TestClient, text: str) -> list[tuple[str, dict]]:
    r = client.post("/api/chat", json={"text": text}, headers=POST_HEADERS)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = []
    for chunk in r.text.strip().split("\n\n"):
        name, data = chunk.split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def test_events_stream_in_order_and_the_tool_writes_to_data_app(data_dir):
    client = make_client(data_dir, Clock(THU_0900))
    events = send(client, "I live in Jerusalem, ready 10 minutes before candles.")
    assert [e for e, _ in events] == ["start", "tool", "text", "done"]
    start, tool, text, done = (d for _, d in events)
    assert start == {"turn": 1, "friday": "2026-10-30", "resumed": False}
    assert tool == {"name": "update_profile", "label": "Saving your profile"}
    assert text == {"delta": "Here is your plan."}
    assert done["reply"] == "Here is your plan." and done["tools"] == ["update_profile"]
    assert done["changed"] == ["candles", "profile", "timeline"]
    assert done["cost_usd"] > 0 and done["session_id"] == "sess-1" and done["is_error"] is False
    store = DataStore(data_dir)
    assert mem.load_profile(store).city == "Jerusalem"
    assert store.read_json(SESSION_FILE) == {"session_id": "sess-1"}
    assert client.get("/api/profile").json()["ready_buffer_min"] == 10


def test_second_message_and_a_restart_resume_the_same_session(data_dir):
    clock = Clock(THU_0900)
    client = make_client(data_dir, clock)
    send(client, "profile")
    clock.now = THU_0905
    assert send(client, "tasks")[0][1]["resumed"] is True
    restarted = make_client(data_dir, Clock(THU_0910))  # a new process: only the files remain
    events = send(restarted, "checklist")
    assert events[0][1] == {"turn": 3, "friday": "2026-10-30", "resumed": True}
    first, second, third = FakeClaudeCode.instances
    assert first.options.resume is None
    assert second.options.resume == third.options.resume == "sess-1"
    assert len(mem.load_checklist(DataStore(data_dir))) == 4


def test_a_new_friday_starts_a_fresh_session_and_marks_the_week(data_dir):
    clock = Clock(THU_0900)
    client = make_client(data_dir, clock)
    send(client, "profile")
    clock.now = THU_0905
    send(client, "tasks")
    week1_cost = client.get("/api/history").json()["week_cost_usd"]
    clock.now = NEXT_THU
    events = send(client, "Plan this Friday.")
    assert events[0][1] == {"turn": 3, "friday": "2026-11-06", "resumed": False}
    assert FakeClaudeCode.instances[-1].options.resume is None
    assert events[-1][1]["changed"] == ["candles", "timeline"]
    assert events[-1][1]["week_cost_usd"] == pytest.approx(events[-1][1]["cost_usd"], abs=1e-4)
    assert week1_cost > events[-1][1]["cost_usd"]
    history = client.get("/api/history").json()["messages"]
    assert [m["type"] for m in history] == ["week", "user", "assistant", "user", "assistant",
                                            "week", "user", "assistant"]
    assert [m["friday"] for m in history if m["type"] == "week"] == ["2026-10-30", "2026-11-06"]
    state = DataStore(data_dir).read_json(STATE)
    assert state["friday"] == "2026-11-06" and state["session"] == 2


def test_new_conversation_keeps_memory_and_drops_the_session(data_dir):
    clock = Clock(THU_0900)
    client = make_client(data_dir, clock)
    send(client, "profile")
    r = client.post("/api/session/new", json={}, headers=POST_HEADERS)
    assert r.status_code == 200
    store = DataStore(data_dir)
    assert not store.exists(SESSION_FILE) and mem.load_profile(store) is not None
    clock.now = THU_0905
    assert send(client, "tasks")[0][1]["resumed"] is False
    assert FakeClaudeCode.instances[-1].options.resume is None
    assert [m["type"] for m in store.read_jsonl(HISTORY)][-3:] == ["reset", "user", "assistant"]


def test_streamed_deltas_are_not_sent_twice(data_dir):
    client = make_client(data_dir, Clock(THU_0900), fake=FakeStreamingClaudeCode)
    events = send(client, "profile")
    deltas = [d["delta"] for e, d in events if e == "text"]
    assert deltas == ["Here is ", "your plan."]
    assert events[-1][1]["reply"] == "Here is your plan."
    assert FakeClaudeCode.instances[0].options.include_partial_messages is True


def test_text_from_two_model_calls_is_kept_apart():
    sent = []
    tee = TeeClient(None, lambda e, d: sent.append(d["delta"]))
    for mid, text in (("m1", "Checking."), ("m2", "Done.")):
        tee.observe(AssistantMessage([TextBlock(text)], "claude-opus-5-5", message_id=mid))
    assert sent == ["Checking.", "\n\nDone."] and tee.text == "Checking.\n\nDone."


def test_history_survives_a_restart(data_dir):
    send(make_client(data_dir, Clock(THU_0900)), "I live in Jerusalem.")
    msgs = make_client(data_dir, Clock(THU_0905)).get("/api/history").json()["messages"]
    assert [(m["type"], m.get("text")) for m in msgs] == [
        ("week", None), ("user", "I live in Jerusalem."), ("assistant", "Here is your plan.")]
    assert msgs[2]["cost_usd"] > 0 and msgs[2]["tools"] == ["update_profile"]


def test_busy_service_refuses_a_second_message(data_dir):
    client = make_client(data_dir, Clock(THU_0900))

    class Running:
        def done(self):
            return False

    client.app.state.chat.task = Running()
    r = client.post("/api/chat", json={"text": "hi"}, headers=POST_HEADERS)
    assert r.status_code == 409
    assert client.post("/api/session/new", json={}, headers=POST_HEADERS).status_code == 409
    assert client.get("/api/health").json()["busy"] is True


async def test_turn_finishes_even_if_nobody_reads_the_stream(data_dir):
    store = DataStore(data_dir)
    service = ChatService(store, Clock(THU_0900), FakeHebcal(),
                          lambda o, t: FakeClaudeCode(o, t, CALLS_BY_NOW))
    service.start("profile")  # the queue is dropped, as when the browser tab closes
    await service.task
    assert store.read_jsonl(HISTORY)[-1]["type"] == "assistant"
    assert mem.load_profile(store) is not None and not service.busy


async def test_failure_reaches_the_page_and_the_history(data_dir):
    def broken(options, tools):
        raise RuntimeError("Claude Code is not logged in")
    service = ChatService(DataStore(data_dir), Clock(THU_0900), FakeHebcal(), broken)
    queue = service.start("hi")
    await service.task
    items = []
    while (item := queue.get_nowait()) is not None:
        items.append(item)
    assert items[-1] == ("error", {"message": "RuntimeError: Claude Code is not logged in"})
    assert DataStore(data_dir).read_jsonl(HISTORY)[-1]["type"] == "error"
    assert not service.busy


@pytest.mark.parametrize("body, headers, status", [
    ({"text": ""}, POST_HEADERS, 422),
    ({"text": "   "}, POST_HEADERS, 422),
    ({"text": "x" * 4001}, POST_HEADERS, 422),
    ({"text": "hi"}, {"Origin": "https://evil.example"}, 403),
])
def test_bad_chat_requests_never_reach_the_agent(data_dir, body, headers, status):
    client = make_client(data_dir, Clock(THU_0900))
    assert client.post("/api/chat", json=body, headers=headers).status_code == status
    assert FakeClaudeCode.instances == []


def test_app_agent_only_adds_partial_messages(tmp_path):
    store = DataStore(tmp_path / "data" / "app")
    base, app = SdkAgent(store, 1, "sess-1").options(), AppAgent(store, 1, "sess-1").options()
    assert app.include_partial_messages is True and base.include_partial_messages is False
    skip = {"include_partial_messages", "hooks", "mcp_servers"}  # hold per-instance objects
    for f in dataclasses.fields(base):
        if f.name not in skip:
            assert getattr(app, f.name) == getattr(base, f.name), f.name
    assert set(app.hooks) == set(base.hooks) and list(app.mcp_servers) == list(base.mcp_servers)
