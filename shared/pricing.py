from __future__ import annotations

# USD per million tokens, from the Claude API price table (checked 2026-10-22).
# Both versions price usage with this one table, so their costs are comparable
# even though the SDK also reports its own estimate.
# Cache writes cost 1.25x input for the 5-minute TTL and 2x for the 1-hour TTL. The API
# version uses 5 minutes; Claude Code writes 1-hour entries, so both rates are needed.
# Opus 1h = $8 reproduces the SDK's own costUSD exactly (results/sdk.json, first SDK run).
# Haiku runs as a Claude Code helper; its cache rates follow the same multipliers.
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20,
                        "cache_write_5m": 5.00, "cache_write_1h": 8.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01,
                         "cache_write_5m": 0.125, "cache_write_1h": 0.20},
}


def cost_usd(model: str, input: int, cache_read: int, cache_write: int, output: int,
             cache_write_1h: int = 0) -> float:
    p = PRICES[model]
    total = (input * p["input"] + cache_read * p["cache_read"]
             + (cache_write - cache_write_1h) * p["cache_write_5m"]
             + cache_write_1h * p["cache_write_1h"] + output * p["output"])
    return round(total / 1_000_000, 6)
