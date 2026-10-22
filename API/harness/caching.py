from __future__ import annotations

import copy

BREAKPOINT = {"type": "ephemeral"}


def log(msg: str) -> None:
    print(f"[caching.py] {msg}", flush=True)


def apply(request: dict) -> dict:
    # Work on a copy: markers must never reach the stored history, or the next
    # request would carry an old marker and change bytes the cache already holds.
    req = copy.deepcopy(request)
    marks = []
    # Tools render before system, so one marker at the end of system covers both.
    if req.get("system"):
        req["system"][-1]["cache_control"] = BREAKPOINT
        marks.append("system")
    messages = req.get("messages") or []
    if messages:
        last = messages[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1]["cache_control"] = BREAKPOINT
        marks.append(f"msg[{len(messages) - 1}].content[-1]")
    log(f"breakpoints: {', '.join(marks) or 'none'}")
    return req
