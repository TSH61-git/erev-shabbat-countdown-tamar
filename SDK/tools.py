from __future__ import annotations

from typing import Callable

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from shared.domain import ToolError
from shared.tool_specs import TOOL_NAMES, TOOL_SPECS
from shared.tools_impl import ToolContext, run_tool

SERVER = "shabbat"


def wrap(spec: dict, current_ctx: Callable[[], ToolContext]) -> SdkMcpTool:
    @tool(spec["name"], spec["description"], spec["input_schema"])  # replaces: tools.py
    async def handler(args: dict) -> dict:
        try:  # replaces: tools.py
            out = run_tool(current_ctx(), spec["name"], args)  # replaces: tools.py
        except ToolError as e:  # replaces: tools.py
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}  # replaces: tools.py
        return {"content": [{"type": "text", "text": out}]}  # replaces: tools.py

    return handler


def build_server(current_ctx: Callable[[], ToolContext]) -> tuple[dict, list[SdkMcpTool]]:
    tools = [wrap(spec, current_ctx) for spec in TOOL_SPECS]  # replaces: tools.py
    return create_sdk_mcp_server(SERVER, "1.0.0", tools), tools  # replaces: tools.py


def allowed_tool_names() -> list[str]:
    return [f"mcp__{SERVER}__{name}" for name in TOOL_NAMES]  # replaces: tools.py
