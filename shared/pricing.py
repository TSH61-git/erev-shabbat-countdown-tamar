from __future__ import annotations

# USD per million tokens, from the Claude API price table (checked 2026-10-22).
# Both versions price usage with this one table, so their costs are comparable
# even though the SDK also reports its own estimate.
# cache_write is the 5-minute TTL rate (1.25x input); we never use the 1-hour TTL.
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
}


def cost_usd(model: str, input: int, cache_read: int, cache_write: int, output: int) -> float:
    p = PRICES[model]
    total = (input * p["input"] + cache_read * p["cache_read"]
             + cache_write * p["cache_write"] + output * p["output"])
    return round(total / 1_000_000, 6)
