from __future__ import annotations

import json

from API.harness.history import History
from API.harness.tokens import TokenMeter

SUMMARY_SYSTEM = (
    "You summarize a conversation between a user and an Erev Shabbat planning assistant so "
    "the assistant can continue without it. Keep: decisions the user made, approvals and "
    "rejections, open questions, and anything promised but not done yet. Skip what is already "
    "saved by tools (profile, tasks, checklist, learned durations, week overrides): the "
    "assistant can read those again. Plain text, at most 150 words."
)


def log(msg: str) -> None:
    print(f"[compaction.py] {msg}", flush=True)


def should_compact(prompt_tokens: int, threshold: int) -> bool:
    if prompt_tokens > threshold:
        log(f"last prompt {prompt_tokens:,} tokens > {threshold:,} -> compacting")
        return True
    log(f"last prompt {prompt_tokens:,} tokens <= {threshold:,}, no compaction")
    return False


def transcript(messages: list[dict]) -> str:
    # Thinking blocks are left out: they are bound to the old conversation and the
    # summary request is a new one.
    lines = []
    for m in messages:
        for b in m["content"]:
            if b["type"] == "text":
                lines.append(f"{m['role']}: {b['text']}")
            elif b["type"] == "tool_use":
                lines.append(f"assistant called {b['name']} {json.dumps(b['input'], ensure_ascii=False)[:300]}")
            elif b["type"] == "tool_result":
                content = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                status = "error" if b.get("is_error") else "result"
                lines.append(f"tool {status}: {content[:300]}")
    return "\n".join(lines)


def compact(client: object, base_request: dict, history: History, meter: TokenMeter,
            turn: int) -> str:
    summary, records = history.active_records()
    messages = history.load_active()
    text = transcript(messages)
    request = {k: v for k, v in base_request.items() if k not in ("tools", "tool_choice")}
    request.update(system=[{"type": "text", "text": SUMMARY_SYSTEM}],
                   messages=[{"role": "user", "content": text}])
    response = client.messages.create(**request)
    meter.record(turn, response.usage)
    new_summary = "".join(b.text for b in response.content if b.type == "text").strip()
    first, last = (records[0]["seq"], records[-1]["seq"]) if records else (0, 0)
    history.append_compaction(new_summary, first, last)
    log(f"summarized {len(messages)} messages ({len(text):,} chars) into {len(new_summary)} chars; "
        f"the next request starts from the summary")
    return new_summary
