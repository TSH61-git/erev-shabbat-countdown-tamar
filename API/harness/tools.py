from __future__ import annotations

import copy
import json

from shared.domain import ToolError
from shared.tool_specs import TOOL_SPECS
from shared.tools_impl import ToolContext, run_tool


def log(msg: str) -> None:
    print(f"[tools.py] {msg}", flush=True)


def api_tool_defs() -> list[dict]:
    # Same list in the same order on every request: tools are the first bytes of
    # the cached prefix.
    return copy.deepcopy(TOOL_SPECS)


def dispatch(ctx: ToolContext, block: object) -> dict:
    name, args, tool_id = block.name, block.input, block.id
    if isinstance(args, str):
        args = json.loads(args)
    shown = json.dumps(args, ensure_ascii=False)
    log(f"dispatch {name} {shown[:100]}{'...' if len(shown) > 100 else ''}")
    try:
        out = run_tool(ctx, name, args)
    except ToolError as e:
        log(f"{name} ERROR -> is_error: {e}")
        return {"type": "tool_result", "tool_use_id": tool_id, "content": str(e), "is_error": True}
    except Exception as e:
        # A bug in a tool should reach the model as a failed call, not kill the run.
        log(f"{name} CRASH -> is_error: {type(e).__name__}: {e}")
        return {"type": "tool_result", "tool_use_id": tool_id, "is_error": True,
                "content": f"internal error in {name}: {type(e).__name__}"}
    log(f"{name} ok ({len(out.splitlines())} lines, {len(out)} chars)")
    return {"type": "tool_result", "tool_use_id": tool_id, "content": out}
