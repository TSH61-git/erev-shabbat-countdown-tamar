from __future__ import annotations

import asyncio
import dataclasses
from typing import Callable

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                              StreamEvent, TextBlock, ToolUseBlock)

from backend.views import Clock
from SDK.agent import SESSION_FILE, ClientFactory, SdkAgent
from SDK.tools import SERVER
from shared import hebcal
from shared.context_line import this_friday
from shared.scenario import Turn
from shared.store import DataStore

STATE = "app_state.json"
HISTORY = "chat.jsonl"
HISTORY_SHOWN = 300

Emit = Callable[[str, dict], None]

TOOL_LABELS = {
    "get_shabbat_times": "Checking candle lighting",
    "update_profile": "Saving your profile",
    "upsert_tasks": "Saving tasks",
    "list_tasks": "Reading your tasks",
    "remove_task": "Removing a task",
    "set_checklist": "Saving the checklist",
    "remove_checklist_item": "Updating the checklist",
    "log_actuals": "Learning real durations",
    "set_week_override": "Saving this week's change",
    "build_timeline": "Building the timeline",
}
# What each tool can change, so the page refreshes only the panels that may be stale.
TOOL_CHANGES = {
    "get_shabbat_times": {"candles"},
    "update_profile": {"profile", "candles", "timeline"},
    "upsert_tasks": {"tasks", "timeline"},
    "remove_task": {"tasks", "timeline"},
    "set_checklist": {"checklist", "timeline"},
    "remove_checklist_item": {"checklist", "timeline"},
    "log_actuals": {"tasks", "timeline"},
    "set_week_override": {"week", "timeline"},
    "build_timeline": {"timeline"},
}


class Busy(RuntimeError):
    pass


class AppAgent(SdkAgent):
    def options(self) -> ClaudeAgentOptions:
        # Only addition to the scenario's configuration: token deltas for the live reply.
        # ask() ignores StreamEvent messages, so its accounting is unchanged.
        return dataclasses.replace(super().options(), include_partial_messages=True)


class TeeClient:
    # Wraps the real client so SdkAgent.ask() runs untouched while the browser sees
    # the same messages as they arrive.

    def __init__(self, inner, emit: Emit):
        self.inner, self.emit = inner, emit
        self.streamed_ids: set[str] = set()
        self.current_id: str | None = None
        self.last_text_id: str | None = None
        self.parts: list[str] = []
        self.result: ResultMessage | None = None

    async def connect(self) -> None:
        await self.inner.connect()

    async def disconnect(self) -> None:
        await self.inner.disconnect()

    async def query(self, prompt: str) -> None:
        await self.inner.query(prompt)

    async def receive_response(self):
        async for msg in self.inner.receive_response():
            self.observe(msg)
            yield msg

    @property
    def text(self) -> str:
        return "".join(self.parts).strip()

    def _text(self, message_id: str | None, delta: str) -> None:
        # Text before and after a tool call belongs to different model calls; keep them apart.
        if self.parts and message_id != self.last_text_id:
            delta = "\n\n" + delta
        self.last_text_id = message_id
        self.parts.append(delta)
        self.emit("text", {"delta": delta})

    def observe(self, msg: object) -> None:
        if isinstance(msg, StreamEvent):
            event = msg.event
            if event.get("type") == "message_start":
                self.current_id = (event.get("message") or {}).get("id")
            elif event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    self.streamed_ids.add(self.current_id)
                    self._text(self.current_id, delta["text"])
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    name = block.name.removeprefix(f"mcp__{SERVER}__")
                    self.emit("tool", {"name": name, "label": TOOL_LABELS.get(name, name)})
                # The full block repeats text already streamed as deltas; send it only
                # when this message had no deltas.
                elif isinstance(block, TextBlock) and msg.message_id not in self.streamed_ids:
                    self._text(msg.message_id, block.text)
        elif isinstance(msg, ResultMessage):
            self.result = msg


def default_client(options: ClaudeAgentOptions, tools: list) -> object:
    return ClaudeSDKClient(options)


class ChatService:
    def __init__(self, store: DataStore, clock: Clock, fetch: hebcal.Fetch,
                 client_factory: ClientFactory | None = None):
        self.store, self.clock, self.fetch = store, clock, fetch
        self.client_factory = client_factory or default_client
        self.task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def state(self) -> dict:
        return self.store.read_json(STATE, {"friday": None, "session": 0, "turn": 0,
                                            "carry": {}, "week_cost_usd": 0.0})

    def history(self) -> dict:
        return {"messages": self.store.read_jsonl(HISTORY)[-HISTORY_SHOWN:], "busy": self.busy,
                "week_cost_usd": round(self.state()["week_cost_usd"], 4)}

    def _fresh_session(self, state: dict, kind: str, now) -> dict:
        self.store.delete(SESSION_FILE)
        state = {**state, "session": state["session"] + 1, "carry": {}}
        self.store.append_jsonl(HISTORY, {"type": kind, "friday": this_friday(now).isoformat(),
                                          "at": now.isoformat(timespec="minutes")})
        return state

    def new_conversation(self) -> None:
        if self.busy:
            raise Busy("a reply is still being written")
        self.store.write_json(STATE, self._fresh_session(self.state(), "reset", self.clock()))

    def start(self, text: str) -> asyncio.Queue:
        if self.busy:
            raise Busy("a reply is still being written")
        queue: asyncio.Queue = asyncio.Queue()
        # A task, not work inside the response: if the browser goes away mid-reply the
        # turn still finishes, so saved files and history are never half-written.
        self.task = asyncio.create_task(self._run(text, queue))
        return queue

    async def _run(self, text: str, queue: asyncio.Queue) -> None:
        def emit(event: str, data: dict) -> None:
            queue.put_nowait((event, data))
        try:
            await self._turn(text, emit)
        except Exception as e:  # noqa: BLE001 - any failure must reach the page, not just the log
            print(f"[chat.py] turn failed: {type(e).__name__}: {e}", flush=True)
            now = self.clock()
            self.store.append_jsonl(HISTORY, {"type": "error", "text": f"{type(e).__name__}: {e}",
                                              "at": now.isoformat(timespec="minutes")})
            emit("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            queue.put_nowait(None)

    async def _turn(self, text: str, emit: Emit) -> None:
        now = self.clock()
        friday = this_friday(now).isoformat()
        state = self.state()
        if state["friday"] != friday:
            # Memory lives in the files, so a new week starts a small, fast session
            # instead of carrying last week's whole conversation.
            state = {**self._fresh_session(state, "week", now), "friday": friday, "week_cost_usd": 0.0}
        resume = (self.store.read_json(SESSION_FILE) or {}).get("session_id")
        turn_no = state["turn"] + 1
        self.store.append_jsonl(HISTORY, {"type": "user", "text": text, "turn": turn_no,
                                          "at": now.isoformat(timespec="minutes")})
        emit("start", {"turn": turn_no, "friday": friday, "resumed": resume is not None})

        tee: TeeClient | None = None

        def factory(options: ClaudeAgentOptions, tools: list) -> TeeClient:
            nonlocal tee
            tee = TeeClient(self.client_factory(options, tools), emit)
            return tee

        agent = AppAgent(self.store, state["session"], resume, self.fetch, factory, state["carry"])
        async with agent:
            record = await agent.ask(Turn(state["session"], turn_no, now, friday, text))
        result = tee.result
        reply = tee.text or (result.result or "").strip()
        is_error = bool(result and result.is_error)
        state = {**state, "turn": turn_no, "carry": agent.carry(),
                 "week_cost_usd": state["week_cost_usd"] + record.cost_usd}
        self.store.write_json(STATE, state)
        changed = sorted(set().union(*(TOOL_CHANGES.get(t, set()) for t in record.tools)))
        self.store.append_jsonl(HISTORY, {"type": "assistant", "text": reply, "turn": turn_no,
                                          "tools": record.tools, "cost_usd": record.cost_usd,
                                          "is_error": is_error,
                                          "at": self.clock().isoformat(timespec="minutes")})
        emit("done", {"turn": turn_no, "reply": reply, "is_error": is_error, "tools": record.tools,
                      "changed": changed, "cost_usd": record.cost_usd,
                      "week_cost_usd": round(state["week_cost_usd"], 4),
                      "session_id": agent.session_id, "seconds": record.seconds})
