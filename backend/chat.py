from __future__ import annotations

import asyncio
import dataclasses
from typing import Callable

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
                              ResultMessage, StreamEvent, TextBlock, ToolUseBlock)

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
PRIVATE_FIELDS = {"cost_usd", "seconds"}

Emit = Callable[[str, dict], None]

# Added to the shared system prompt in the app only, so the assignment's scenario keeps
# its prompt (and its cache) exactly as measured.
APP_RULES = """

# Rules for this app
- Every task is kind "active". Never create passive tasks: the screen shows one task at a time.
  Waiting time (oven, dough rising, slow cooking) goes into the task's own minutes, or becomes
  its own active task.
- When the user writes in Hebrew, reply in Hebrew and address her in the feminine.
- Task names may be Hebrew; use _ instead of spaces (e.g. להכין_חצילים)."""

PASSIVE_REFUSED = ("Passive tasks are not used in this app: send every task with kind \"active\" "
                   "(fold waiting time into the minutes). / במסך הזה כל המשימות פעילות.")
GUARDED_TOOLS = f"mcp__{SERVER}__upsert_tasks|mcp__{SERVER}__set_week_override"

TOOL_LABELS = {
    "get_shabbat_times": "בדיקת זמן הדלקת נרות",
    "update_profile": "שמירת הפרטים",
    "upsert_tasks": "שמירת משימות",
    "list_tasks": "קריאת המשימות",
    "remove_task": "מחיקת משימה",
    "set_checklist": "שמירת הדברים האחרונים",
    "remove_checklist_item": "עדכון הדברים האחרונים",
    "log_actuals": "למידת הזמנים האמיתיים",
    "set_week_override": "שמירת שינוי לשבוע הזה",
    "build_timeline": "בניית לוח הזמנים",
}
REPLAN_LABEL = "בונה מחדש את לוח הזמנים"


def tool_label(name: str, args: dict | None) -> str:
    args = args or {}
    if name == "build_timeline" and (args.get("now") or args.get("delay_minutes")):
        return REPLAN_LABEL
    return TOOL_LABELS.get(name, name)


def passive_tasks(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name.endswith("__upsert_tasks"):
        tasks = tool_input.get("tasks") or []
    else:
        change = tool_input.get("change") or {}
        tasks = [change.get("new_task") or {}] if change.get("type") == "add_task" else []
    return [str(t.get("name")) for t in tasks if isinstance(t, dict) and t.get("kind") == "passive"]


async def refuse_passive(hook_input: dict, tool_use_id: str | None, context: object) -> dict:
    # Enforced, not only asked: one passive task would put two rows at the same time.
    names = passive_tasks(hook_input.get("tool_name", ""), hook_input.get("tool_input") or {})
    if not names:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": f"{PASSIVE_REFUSED} ({', '.join(names)})"}}
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
        base = super().options()
        hooks = {**base.hooks, "PreToolUse": [HookMatcher(matcher=GUARDED_TOOLS, hooks=[refuse_passive])]}
        # Token deltas for the live reply; ask() ignores StreamEvent messages, so its
        # accounting is unchanged.
        return dataclasses.replace(base, include_partial_messages=True, hooks=hooks,
                                   system_prompt=base.system_prompt + APP_RULES)


class TeeClient:
    # Wraps the real client so SdkAgent.ask() runs untouched while the browser sees
    # the same messages as they arrive.

    def __init__(self, inner, emit: Emit):
        self.inner, self.emit = inner, emit
        self.streamed_ids: set[str] = set()
        self.current_id: str | None = None
        self.last_text_id: str | None = None
        self.parts: list[str] = []
        self.labels: list[str] = []
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
                    label = tool_label(name, block.input)
                    self.labels.append(label)
                    self.emit("tool", {"name": name, "label": label})
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
        # Cost stays in the saved files and the server log; the app runs on the user's
        # subscription, so the page never shows it.
        messages = [{k: v for k, v in m.items() if k not in PRIVATE_FIELDS}
                    for m in self.store.read_jsonl(HISTORY)[-HISTORY_SHOWN:]]
        return {"messages": messages, "busy": self.busy}

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
                                          "tools": record.tools, "tool_labels": list(dict.fromkeys(tee.labels)),
                                          "cost_usd": record.cost_usd,
                                          "seconds": record.seconds,
                                          "is_error": is_error,
                                          "at": self.clock().isoformat(timespec="minutes")})
        print(f"[chat.py] turn {turn_no}: est. ${record.cost_usd:.4f}, {record.seconds:.1f} s, "
              f"week est. ${state['week_cost_usd']:.4f}", flush=True)
        emit("done", {"turn": turn_no, "reply": reply, "is_error": is_error, "tools": record.tools,
                      "changed": changed, "session_id": agent.session_id})
