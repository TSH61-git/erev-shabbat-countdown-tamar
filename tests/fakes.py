from __future__ import annotations

import json

from shared.config import PROJECT_ROOT


def hebcal_fixture(friday: str) -> dict:
    path = PROJECT_ROOT / "shared" / "fixtures" / f"hebcal_{friday}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class FakeHebcal:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, params: dict) -> dict:
        self.calls.append(params)
        return hebcal_fixture(f"{params['gy']}-{params['gm']:02d}-{params['gd']:02d}")


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def tool_use(tool_id: str, name: str, args: dict) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def thinking(signature: str = "sig==") -> dict:
    return {"type": "thinking", "thinking": "", "signature": signature}


def message(content: list[dict], stop_reason: str = "end_turn", input_tokens: int = 100,
            output_tokens: int = 20, cache_read: int | None = 0, cache_write: int | None = 0,
            **extra) -> "Message":
    from anthropic.types import Message
    return Message.model_validate({
        "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_read_input_tokens": cache_read,
                  "cache_creation_input_tokens": cache_write},
        **extra})


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[dict] = []

    def create(self, **request):
        import copy
        self.requests.append(copy.deepcopy(request))
        if not self.script:
            raise AssertionError("FakeAnthropicClient: no scripted response left")
        item = self.script.pop(0)
        return item(request) if callable(item) else item

    def count_tokens(self, **request):
        from types import SimpleNamespace
        return SimpleNamespace(input_tokens=len(str(request)) // 4)


class FakeAnthropicClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)


class FakeClaudeCode:
    instances: list["FakeClaudeCode"] = []

    def __init__(self, options, tools, calls_by_now: dict, session_id: str = "sess-1"):
        self.options, self.calls_by_now = options, calls_by_now
        self.handlers = {t.name: t.handler for t in tools}
        self.session_id = options.resume or session_id
        self.cost = 0.0
        self.prompt = ""
        FakeClaudeCode.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt: str):
        self.prompt = prompt

    async def receive_response(self):
        import re
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
        hook = self.options.hooks["UserPromptSubmit"][0].hooks[0]
        out = await hook({"prompt": self.prompt}, None, None)
        line = out["hookSpecificOutput"]["additionalContext"]
        calls = self.calls_by_now[re.search(r"now: (.+?) \|", line).group(1)]
        usage = {"input_tokens": 5, "cache_read_input_tokens": 9000,
                 "cache_creation_input_tokens": 300, "output_tokens": 200}
        uses = [ToolUseBlock(f"tu_{i}", f"mcp__shabbat__{n}", a) for i, (n, a) in enumerate(calls)]
        yield AssistantMessage(uses[:1], "claude-opus-5-5", usage=usage, message_id="m1")
        yield AssistantMessage(uses[1:], "claude-opus-5-5", usage=usage, message_id="m1")
        for (n, a) in calls:
            await self.handlers[n](a)
        yield AssistantMessage([TextBlock("Here is your plan.")], "claude-opus-5-5",
                               usage=usage, message_id="m2")
        self.cost += 0.01
        yield ResultMessage("success", 10, 10, False, 2, self.session_id,
                            total_cost_usd=round(self.cost, 6), usage=usage,
                            result="Here is your plan.")
