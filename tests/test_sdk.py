from __future__ import annotations

import json
from datetime import datetime

import pytest

from SDK.agent import SdkAgent, render_claude_md
from SDK.main import main as sdk_main, run_async
from SDK.tools import allowed_tool_names
from shared import memory_files as mem
from shared.config import PROJECT_ROOT
from shared.context_line import render_context
from shared.prompts import SYSTEM_PROMPT
from shared.store import DataStore
from shared.tools_impl import ToolContext
from tests.fakes import FakeClaudeCode, FakeHebcal
from tests.sample_data import TASKS
from tests.scripted_run import IDEAL_CALLS

CALLS_BY_NOW = {datetime.fromisoformat(now).strftime("%a %Y-%m-%d %H:%M"): calls
                for (_, _, now), calls in IDEAL_CALLS.items()}


@pytest.fixture
def store(tmp_path) -> DataStore:
    s = DataStore(tmp_path / "data" / "sdk")
    s.reset()
    return s


def test_options_match_the_plan(store):
    opts = SdkAgent(store, session=2, resume="sess-1").options()
    assert (opts.model, opts.effort, opts.thinking) == ("claude-opus-5-5", "medium", {"type": "adaptive"})
    assert opts.system_prompt == SYSTEM_PROMPT
    assert opts.tools == [] and opts.strict_mcp_config is True
    assert opts.allowed_tools == allowed_tool_names() and len(opts.allowed_tools) == 10
    assert opts.permission_mode == "dontAsk" and opts.setting_sources == ["project"]
    assert opts.cwd == str(store.path("workspace")) and opts.resume == "sess-1"
    assert set(opts.hooks) == {"UserPromptSubmit", "PreCompact"}
    assert list(opts.mcp_servers) == ["shabbat"]


async def test_tool_wrapper_success_and_error(store):
    agent = SdkAgent(store, session=1, fetch=FakeHebcal())
    agent.ctx = ToolContext(store, datetime(2026, 10, 29, 9, 0), 2, 1, FakeHebcal())
    handlers = {t.name: t.handler for t in agent.tools}
    await handlers["update_profile"]({"city": "Jerusalem", "ready_buffer_min": 10})
    ok = await handlers["upsert_tasks"]({"tasks": TASKS})
    assert ok == {"content": [{"type": "text", "text": "saved: added 10. 10 tasks total"}]}
    bad = await handlers["remove_task"]({"name": "dessert"})
    assert bad["is_error"] is True and "no task named dessert" in bad["content"][0]["text"]
    assert store.read_jsonl("tool_log.jsonl")[-1]["ok"] is False


async def test_context_hook_returns_the_shared_context_line(store):
    agent = SdkAgent(store, session=1)
    agent.ctx = ToolContext(store, datetime(2026, 11, 5, 9, 0))
    out = await agent.inject_context({"prompt": "Plan this Friday."}, None, None)
    assert out == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": render_context(store, agent.ctx.now)}}


def test_claude_md_summary(store):
    assert "Nothing saved yet" in render_claude_md(store)
    mem.update_profile(store, city="Jerusalem", ready_buffer_min=10, friday_start="12:00")
    mem.upsert_tasks(store, TASKS)
    mem.log_actuals(store, "2026-10-30", [{"task": "challah", "minutes": 90}])
    text = render_claude_md(store)
    assert "City: Jerusalem, ready 10 min" in text
    assert "Tasks: 10 saved; learned durations for: challah" in text
    assert "shopping" not in text


async def test_full_scenario_offline(tmp_path):
    FakeClaudeCode.instances.clear()
    out = tmp_path / "results" / "sdk.json"
    results = await run_async(str(PROJECT_ROOT / "scenario.json"), str(out),
                              data_dir=str(tmp_path / "data" / "sdk"), fetch=FakeHebcal(),
                              client_factory=lambda o, t: FakeClaudeCode(o, t, CALLS_BY_NOW))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["checks_passed"] == "7/7", saved["checks"]
    assert [t["model_calls"] for t in saved["turns"]] == [2] * 8
    assert saved["turns"][6]["tools"] == ["set_week_override", "set_week_override", "build_timeline"]
    first, second = FakeClaudeCode.instances
    assert first.options.resume is None and second.options.resume == "sess-1"
    assert saved["extra"]["session_ids"] == ["sess-1", "sess-1"]
    assert saved["extra"]["reported_total_cost_usd"] == 0.08
    workspace_md = (tmp_path / "data" / "sdk" / "workspace" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "learned durations for: challah, cleaning" in workspace_md
    loc = saved["loc"]
    assert loc["compaction.py"] == 0 and loc["caching.py"] == 0
    assert all(loc[f] > 0 for f in ["loop.py", "tools.py", "history.py", "memory.py",
                                    "context.py", "tokens.py"])
    assert results["version"] == "sdk"


def test_main_refuses_when_api_key_is_set(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("sys.argv", ["main.py", "--scenario", "scenario.json",
                                     "--out", str(tmp_path / "sdk.json")])
    assert sdk_main() == 2
    assert "ANTHROPIC_API_KEY is set" in capsys.readouterr().out
    assert not (tmp_path / "sdk.json").exists()
