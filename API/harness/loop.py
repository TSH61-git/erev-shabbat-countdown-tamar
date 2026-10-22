from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from API.harness.history import History
from API.harness.tokens import TokenMeter

MAX_CALLS = 10


def log(msg: str) -> None:
    print(f"[loop.py] {msg}", flush=True)


@dataclass
class TurnOutcome:
    text: str
    model_calls: int
    stop_reason: str
    tools: list[str] = field(default_factory=list)


def run_turn(client: object, build_request: Callable[[list[dict]], dict], history: History,
             dispatch: Callable[[object], dict], meter: TokenMeter, turn: int) -> TurnOutcome:
    tools_used: list[str] = []
    for call in range(1, MAX_CALLS + 1):
        response = client.messages.create(**build_request(history.load_active()))
        usage = meter.record(turn, response.usage)
        history.append_message({"role": "assistant", "content": response.content}, usage.prompt)
        uses = [b for b in response.content if b.type == "tool_use"]
        log(f"turn {turn} call {call}: stop_reason={response.stop_reason}"
            + (f" ({len(uses)} tool_use)" if uses else ""))

        if response.stop_reason == "tool_use":
            tools_used += [b.name for b in uses]
            # All results in ONE user message, in tool_use order; splitting them
            # teaches the model to stop calling tools in parallel.
            history.append_message({"role": "user", "content": [dispatch(b) for b in uses]})
            continue

        if uses:
            # Cut off (e.g. max_tokens) after a tool_use: every tool_use still needs
            # a result, or the next request is rejected.
            history.append_message({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                 "content": "not executed: the response was cut off"} for b in uses]})
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            log(f"refusal: category={getattr(details, 'category', None)}")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return TurnOutcome(text or f"[{response.stop_reason}]", call, response.stop_reason, tools_used)

    log(f"turn {turn}: stopped after {MAX_CALLS} calls")
    return TurnOutcome(f"[stopped after {MAX_CALLS} model calls]", MAX_CALLS, "max_calls", tools_used)
