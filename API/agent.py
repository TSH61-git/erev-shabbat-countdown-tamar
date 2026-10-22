from __future__ import annotations

import time

from API.harness import caching, compaction, context, loop, memory, tools
from API.harness.history import History
from API.harness.tokens import TokenMeter
from shared import config, hebcal
from shared.prompts import SYSTEM_PROMPT
from shared.results import TurnRecord
from shared.scenario import Turn
from shared.store import DataStore
from shared.tools_impl import ToolContext

DEFAULT_COMPACT_THRESHOLD = 9000


def log(msg: str) -> None:
    print(f"[agent.py] {msg}", flush=True)


class ApiAgent:
    def __init__(self, client: object, store: DataStore, session: int,
                 fetch: hebcal.Fetch = hebcal.fetch_json,
                 compact_threshold: int = DEFAULT_COMPACT_THRESHOLD):
        self.client, self.store, self.session, self.fetch = client, store, session, fetch
        self.compact_threshold = compact_threshold
        self.meter = TokenMeter(config.MODEL)
        self.history = History(store)
        self.history.start_session(session)
        log(f"new agent instance for session {session}; "
            f"{len(self.history.load_active())} messages reloaded from disk")

    def base_request(self, messages: list[dict]) -> dict:
        return {
            "model": config.MODEL,
            "max_tokens": 16000,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": config.EFFORT},
            "system": [{"type": "text", "text": SYSTEM_PROMPT}],
            "tools": tools.api_tool_defs(),
            "tool_choice": {"type": "auto"},
            "messages": messages,
        }

    def build_request(self, messages: list[dict]) -> dict:
        return caching.apply(self.base_request(messages))

    def ask(self, turn: Turn) -> TurnRecord:
        log(f"turn {turn.turn} (session {turn.session}) at {turn.now:%Y-%m-%d %H:%M}")
        started = time.perf_counter()
        compacted = compaction.should_compact(self.history.last_prompt_tokens(),
                                              self.compact_threshold)
        if compacted:
            compaction.compact(self.client, self.base_request([]), self.history, self.meter,
                               turn.turn)
        view = memory.load(self.store, turn.now)
        self.history.append_message(context.build_user_message(view, turn.text))
        ctx = ToolContext(self.store, turn.now, turn.turn, turn.session, self.fetch)
        outcome = loop.run_turn(self.client, self.build_request, self.history,
                                lambda block: tools.dispatch(ctx, block), self.meter, turn.turn)
        s = self.meter.turn_summary(turn.turn)
        return TurnRecord(turn.turn, turn.session, s["input"], s["cache_read"], s["cache_write"],
                          s["output"], s["model_calls"], outcome.tools, s["cost_usd"],
                          round(time.perf_counter() - started, 2), compacted, outcome.text[:300])
