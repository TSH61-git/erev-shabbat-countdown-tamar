from __future__ import annotations

from API.harness.memory import MemoryView
from shared.context_line import summarize


def log(msg: str) -> None:
    print(f"[context.py] {msg}", flush=True)


def build_user_message(view: MemoryView, text: str) -> dict:
    # The context goes in the user message, not the system prompt: the system
    # prompt must stay byte-identical for caching, and this line changes each turn.
    line = summarize(view.now, view.profile, view.overrides, view.tasks, view.learned,
                     view.checklist)
    log(f"context {len(line)} chars: profile, {len(view.overrides)} overrides, "
        f"{len(view.tasks)}-task summary (task list stays behind list_tasks)")
    return {"role": "user", "content": [{"type": "text", "text": line},
                                        {"type": "text", "text": text}]}
