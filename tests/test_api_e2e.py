from __future__ import annotations

import json
import re
from datetime import datetime

from API.main import run
from shared.config import PROJECT_ROOT
from shared.prompts import SYSTEM_PROMPT
from shared.store import DataStore
from tests.fakes import FakeAnthropicClient, FakeHebcal, message, text, thinking, tool_use
from tests.scripted_run import IDEAL_CALLS


class IdealModel:
    def __init__(self):
        self.by_now = {datetime.fromisoformat(now).strftime("%a %Y-%m-%d %H:%M"): calls
                       for (_, _, now), calls in IDEAL_CALLS.items()}

    def __call__(self, request: dict):
        msgs = request["messages"]
        size = 1500 + 600 * len(msgs)
        usage = {"input_tokens": 200, "cache_read": size - 200, "cache_write": 0}
        if "tools" not in request:
            return message([text("User is in Jerusalem; week 1 planned and reviewed.")], **usage)
        last = msgs[-1]["content"]
        if any(b["type"] == "tool_result" for b in last):
            return message([text("Here is your plan.")], **usage)
        line = next(b["text"] for b in last if b["text"].startswith("<context>"))
        calls = self.by_now[re.search(r"now: (.+?) \|", line).group(1)]
        return message([thinking()] + [tool_use(f"tu_{i}", n, a) for i, (n, a) in enumerate(calls)],
                       stop_reason="tool_use", **usage)


def test_full_scenario_offline(tmp_path, capsys):
    client = FakeAnthropicClient([IdealModel()] * 100)
    out = tmp_path / "results" / "api.json"
    results = run(str(PROJECT_ROOT / "scenario.json"), str(out), client,
                  data_dir=str(tmp_path / "data" / "api"), fetch=FakeHebcal(),
                  compact_threshold=12000)

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(results))
    assert saved["checks_passed"] == "7/7", saved["checks"]
    assert [t["turn"] for t in saved["turns"]] == list(range(1, 9))
    assert saved["turns"][3]["tools"] == ["get_shabbat_times", "build_timeline"]
    assert all(t["model_calls"] >= 2 for t in saved["turns"])
    assert set(saved["loc"]) == {"loop.py", "tools.py", "history.py", "memory.py", "context.py",
                                 "compaction.py", "caching.py", "tokens.py"}
    assert all(v > 0 for v in saved["loc"].values())

    compacted = saved["extra"]["compacted_turns"]
    assert compacted and min(compacted) >= 6
    store = DataStore(tmp_path / "data" / "api")
    assert any(r["type"] == "compaction" for r in store.read_jsonl("history.jsonl"))

    requests = client.messages.requests
    with_tools = [r for r in requests if "tools" in r]
    assert {json.dumps(r["system"][0]["text"]) for r in with_tools} == {json.dumps(SYSTEM_PROMPT)}
    assert all(r["output_config"] == {"effort": "medium"} and r["model"] == "claude-opus-5-5"
               for r in requests)
    after = next(r for r in with_tools if "<summary>" in json.dumps(r["messages"][0]))
    assert "thinking" not in [b["type"] for m in after["messages"][:1] for b in m["content"]]

    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    untagged = [l for l in lines if not re.match(r"^\[[a-z_]+\.py\] ", l)]
    assert untagged == []
    for tag in ["loop.py", "tools.py", "history.py", "memory.py", "context.py", "compaction.py",
                "caching.py", "tokens.py", "agent.py", "main.py"]:
        assert any(l.startswith(f"[{tag}]") for l in lines), tag


def test_preflight_offline(tmp_path, monkeypatch, capsys):
    from API.main import preflight
    from shared import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    assert preflight(FakeAnthropicClient([IdealModel()] * 10)) == 0
    out = capsys.readouterr().out
    assert "cache read on every call after the first: True" in out
    assert (tmp_path / "data" / "api-preflight" / "history.jsonl").exists()
