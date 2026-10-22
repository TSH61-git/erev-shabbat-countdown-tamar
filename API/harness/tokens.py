from __future__ import annotations

from dataclasses import dataclass

from shared.pricing import cost_usd


def log(msg: str) -> None:
    print(f"[tokens.py] {msg}", flush=True)


@dataclass(frozen=True)
class CallUsage:
    turn: int
    input: int
    cache_read: int
    cache_write: int
    output: int
    cost: float

    @property
    def prompt(self) -> int:
        return self.input + self.cache_read + self.cache_write


class TokenMeter:
    def __init__(self, model: str):
        self.model = model
        self.calls: list[CallUsage] = []

    def record(self, turn: int, usage: object) -> CallUsage:
        # The API sends null for cache fields when caching did not apply.
        def get(name: str) -> int:
            return getattr(usage, name, None) or 0

        u = (get("input_tokens"), get("cache_read_input_tokens"),
             get("cache_creation_input_tokens"), get("output_tokens"))
        call = CallUsage(turn, *u, cost=cost_usd(self.model, *u))
        self.calls.append(call)
        log(f"call: in={call.input:,} cache_r={call.cache_read:,} cache_w={call.cache_write:,} "
            f"out={call.output:,} ${call.cost:.4f}")
        return call

    @property
    def last_prompt_tokens(self) -> int:
        return self.calls[-1].prompt if self.calls else 0

    def turn_summary(self, turn: int) -> dict:
        calls = [c for c in self.calls if c.turn == turn]
        s = {"input": sum(c.input for c in calls), "cache_read": sum(c.cache_read for c in calls),
             "cache_write": sum(c.cache_write for c in calls), "output": sum(c.output for c in calls),
             "model_calls": len(calls), "cost_usd": round(sum(c.cost for c in calls), 6)}
        log(f"turn {turn} total: {s['model_calls']} calls, in={s['input']:,} "
            f"cache_r={s['cache_read']:,} cache_w={s['cache_write']:,} out={s['output']:,} "
            f"${s['cost_usd']:.4f}")
        return s
