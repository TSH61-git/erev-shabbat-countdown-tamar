from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
                              ResultMessage, TextBlock, ToolUseBlock)

from SDK.tools import SERVER, allowed_tool_names, build_server
from shared import config, hebcal, memory_files as mem
from shared.context_line import render_context
from shared.pricing import PRICES, cost_usd
from shared.prompts import SYSTEM_PROMPT
from shared.results import TurnRecord
from shared.scenario import Turn
from shared.store import DataStore
from shared.tools_impl import ToolContext

SESSION_FILE = "session.json"

# replaces: compaction.py -> nothing to write: Claude Code compacts on its own near its limit.
# replaces: caching.py -> nothing to write: Claude Code places cache breakpoints itself.

ClientFactory = Callable[[ClaudeAgentOptions, list], object]


def log(msg: str) -> None:
    print(f"[agent.py] {msg}", flush=True)


def render_claude_md(store: DataStore) -> str:
    p = mem.load_profile(store)
    tasks = mem.load_tasks(store)
    learned = sorted(mem.learned_minutes(store))
    lines = ["# Saved memory",
             "Written by the application from the saved files when this session starts.",
             "Details are behind the tools (list_tasks); this is only the summary.", ""]
    if p is None and not tasks:
        return "\n".join(lines + ["- Nothing saved yet."]) + "\n"
    if p:
        lines += [f"- City: {p.city}, ready {p.ready_buffer_min} min before candle lighting",
                  f"- Friday start {p.friday_start}, Thursday night "
                  f"{p.thursday_night_start}-{p.thursday_night_end}"]
    lines.append(f"- Tasks: {len(tasks)} saved; learned durations for: {', '.join(learned) or 'none'}")
    lines.append(f"- Checklist: {len(mem.load_checklist(store))} items")
    return "\n".join(lines) + "\n"


class SdkAgent:
    def __init__(self, store: DataStore, session: int, resume: str | None = None,
                 fetch: hebcal.Fetch = hebcal.fetch_json,
                 client_factory: ClientFactory | None = None, carry: dict | None = None):
        self.store, self.session, self.resume, self.fetch = store, session, resume, fetch
        self.client_factory = client_factory or (lambda options, tools: ClaudeSDKClient(options))
        self.workspace = store.path("workspace")
        self.ctx: ToolContext | None = None
        self.server, self.tools = build_server(lambda: self.ctx)
        self.session_id = resume
        self.compacted = False
        # total_cost_usd and model_usage are running totals that a resumed session
        # continues from the transcript, so session 2 must start from session 1's totals.
        carry = carry or {}
        self.cost_so_far = carry.get("total_cost_usd", 0.0)
        self.model_usage_seen: dict[str, dict] = dict(carry.get("model_usage", {}))
        self.turn_extra: list[dict] = []

    def carry(self) -> dict:
        return {"total_cost_usd": self.cost_so_far, "model_usage": self.model_usage_seen}

    def helper_usage(self, model_usage: dict | None) -> dict[str, dict]:
        # Claude Code also calls a small helper model (seen: claude-haiku-5-5); it shows up
        # only in the running model_usage totals, so take this turn's difference.
        out = {}
        for model, mu in (model_usage or {}).items():
            before = self.model_usage_seen.get(model, {})
            delta = {k: (mu.get(k) or 0) - (before.get(k) or 0)
                     for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                               "cacheCreationInputTokens", "costUSD")}
            self.model_usage_seen[model] = mu
            if model != config.MODEL and any(v > 0 for v in delta.values()):
                if model in PRICES:
                    delta["cost"] = cost_usd(model, delta["inputTokens"],
                                             delta["cacheReadInputTokens"],
                                             delta["cacheCreationInputTokens"], delta["outputTokens"])
                else:
                    delta["cost"] = round(delta["costUSD"], 6)
                out[model] = delta
        return out

    def options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=config.MODEL,
            effort=config.EFFORT,
            thinking={"type": "adaptive"},
            system_prompt=SYSTEM_PROMPT,
            tools=[],
            mcp_servers={SERVER: self.server},  # replaces: tools.py
            allowed_tools=allowed_tool_names(),  # replaces: tools.py
            strict_mcp_config=True,
            permission_mode="dontAsk",
            setting_sources=["project"],  # replaces: memory.py
            cwd=str(self.workspace),  # replaces: memory.py
            resume=self.resume,  # replaces: history.py
            max_turns=15,  # replaces: loop.py
            hooks={
                "UserPromptSubmit": [HookMatcher(hooks=[self.inject_context])],  # replaces: context.py
                "PreCompact": [HookMatcher(hooks=[self.saw_compaction])],
            },
        )

    async def inject_context(self, hook_input: dict, tool_use_id: str | None, context: object) -> dict:
        line = render_context(self.store, self.ctx.now)  # replaces: context.py
        log(f"context hook: {len(line)} chars added to the prompt")
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",  # replaces: context.py
                                       "additionalContext": line}}  # replaces: context.py

    async def saw_compaction(self, hook_input: dict, tool_use_id: str | None, context: object) -> dict:
        # Only observes: Claude Code decides when to compact and does it itself.
        self.compacted = True
        log(f"Claude Code is compacting ({hook_input.get('trigger', '?')})")
        return {}

    async def __aenter__(self) -> "SdkAgent":
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "CLAUDE.md").write_text(render_claude_md(self.store), encoding="utf-8")  # replaces: memory.py
        self.client = self.client_factory(self.options(), self.tools)  # replaces: loop.py
        await self.client.connect()  # replaces: loop.py
        log(f"session {self.session}: Claude Code started in {config.shown(self.workspace)}"
            + (f", resuming {self.resume}" if self.resume else ""))
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.disconnect()

    async def ask(self, turn: Turn) -> TurnRecord:
        log(f"turn {turn.turn} (session {turn.session}) at {turn.now:%Y-%m-%d %H:%M}")
        self.ctx = ToolContext(self.store, turn.now, turn.turn, turn.session, self.fetch)
        self.compacted = False
        started = time.perf_counter()
        usage_by_call: dict[str, dict] = {}
        tools: list[str] = []
        texts: list[str] = []
        result: ResultMessage | None = None
        await self.client.query(turn.text)  # replaces: loop.py
        async for msg in self.client.receive_response():  # replaces: loop.py
            if isinstance(msg, AssistantMessage):
                # One API call arrives as several messages with the same id. Their usage is a
                # stream snapshot (output is cut short), so it is used only to count calls.
                if msg.usage:
                    usage_by_call[msg.message_id or f"call{len(usage_by_call)}"] = msg.usage  # replaces: tokens.py
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tools.append(block.name.removeprefix(f"mcp__{SERVER}__"))
                    elif isinstance(block, TextBlock):
                        texts.append(block.text)
            elif isinstance(msg, ResultMessage):
                result = msg
        if result is None:
            raise RuntimeError(f"turn {turn.turn}: Claude Code ended without a result")
        if result.is_error:
            log(f"turn {turn.turn}: result is_error ({result.subtype}): {result.result}")
        self.session_id = result.session_id
        self.store.write_json(SESSION_FILE, {"session_id": self.session_id})  # replaces: history.py

        # ResultMessage.usage is the main model's complete total for this query (all calls,
        # final output tokens); it matched the SDK's own costUSD on the first run.
        u = result.usage or {}  # replaces: tokens.py
        tok = [u.get("input_tokens") or 0, u.get("cache_read_input_tokens") or 0,  # replaces: tokens.py
               u.get("cache_creation_input_tokens") or 0, u.get("output_tokens") or 0]  # replaces: tokens.py
        w1h = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0  # replaces: tokens.py
        helpers = self.helper_usage(getattr(result, "model_usage", None))  # replaces: tokens.py
        cost = round(cost_usd(config.MODEL, *tok, cache_write_1h=w1h)  # replaces: tokens.py
                     + sum(h["cost"] for h in helpers.values()), 6)  # replaces: tokens.py
        total_now = result.total_cost_usd or 0
        if total_now < self.cost_so_far:
            log("SDK running totals restarted; this turn's reported cost is the new total")
            self.cost_so_far = 0.0
        reported = round(total_now - self.cost_so_far, 6)  # replaces: tokens.py
        self.cost_so_far = total_now
        helper_note = "".join(f", helper {m}: in={h['inputTokens']:,} out={h['outputTokens']:,}"
                              for m, h in helpers.items())
        log(f"turn {turn.turn}: {len(usage_by_call)} model calls (num_turns={result.num_turns}), "
            f"in={tok[0]:,} cache_r={tok[1]:,} cache_w={tok[2]:,} (1h {w1h:,}) out={tok[3]:,}"
            f"{helper_note}, ${cost:.4f} by our prices, ${reported:.4f} reported by the SDK")
        self.turn_extra.append({"turn": turn.turn, "num_turns": result.num_turns,
                                "reported_cost_usd": reported, "helper_models": helpers,
                                "result_usage": result.usage,
                                "model_usage": getattr(result, "model_usage", None),
                                "session_id": result.session_id, "subtype": result.subtype})
        reply = (result.result or "\n".join(texts)).strip()
        return TurnRecord(turn.turn, turn.session, *tok, len(usage_by_call), tools, cost,
                          round(time.perf_counter() - started, 2), self.compacted, reply[:300], w1h)
