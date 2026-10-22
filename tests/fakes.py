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


class FakeAnthropicClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)
