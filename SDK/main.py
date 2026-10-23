from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Run as "python SDK/main.py": Python puts SDK/ on the path, not the project root.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from SDK.agent import ClientFactory, SdkAgent  # noqa: E402
from shared import config, hebcal  # noqa: E402
from shared.checks import run_checks  # noqa: E402
from shared.console import setup_output  # noqa: E402
from shared.results import build_results, library_versions, sdk_loc, write_results  # noqa: E402
from shared.scenario import load_scenario, reset_state  # noqa: E402


def log(msg: str) -> None:
    print(f"[main.py] {msg}", flush=True)


async def run_async(scenario_path: str, out_path: str, data_dir: str | None = None,
                    fetch: hebcal.Fetch = hebcal.fetch_json,
                    client_factory: ClientFactory | None = None) -> dict:
    scenario = load_scenario(scenario_path)
    store = reset_state(config.data_dir("sdk", data_dir))
    log(f"clean state in {config.shown(store.root)}; {len(scenario.turns)} turns in {len(scenario.sessions)} sessions")
    records, extra, resume, session_ids, carry = [], [], None, [], None
    for turns in scenario.sessions:
        async with SdkAgent(store, turns[0].session, resume, fetch, client_factory, carry) as agent:
            for turn in turns:
                record = await agent.ask(turn)
                log(f"turn {turn.turn} reply: {record.reply_preview[:120]!r}")
                records.append(record)
            extra += agent.turn_extra
            resume = agent.session_id
            session_ids.append(agent.session_id)
            carry = agent.carry()
    checks = run_checks(store, scenario.expect)
    for c in checks:
        log(f"check {c.id} {'PASS' if c.passed else 'FAIL'}: {c.name} ({c.detail})")
    from claude_agent_sdk._cli_version import __cli_version__
    results = build_results(
        "sdk", records, checks, sdk_loc(ROOT / "SDK"),
        {**library_versions(["claude-agent-sdk", "requests", "truststore"]),
         "claude-code-cli (bundled)": __cli_version__},
        {"session_ids": session_ids,
         "reported_total_cost_usd": round(sum(e["reported_cost_usd"] for e in extra), 6),
         "sdk_final_total_cost_usd": carry["total_cost_usd"] if carry else 0.0,
         "compacted_turns": [r.turn for r in records if r.compacted],
         "per_turn": extra})
    write_results(out_path, results)
    t = results["totals"]
    log(f"wrote {out_path}: checks {results['checks_passed']}, {t['model_calls']} model calls, "
        f"cache read {t['cache_read_pct']}%, ${t['cost_usd']:.4f} by our prices "
        f"(SDK reports ${results['extra']['reported_total_cost_usd']:.4f})")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Erev Shabbat Countdown on the Claude Agent SDK")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--log", help="also write all output to this file (UTF-8)")
    parser.add_argument("--allow-api-key", action="store_true",
                        help="run even though ANTHROPIC_API_KEY is set (bills the key)")
    args = parser.parse_args()
    setup_output(args.log)
    if os.environ.get("ANTHROPIC_API_KEY") and not args.allow_api_key:
        log("WARNING: ANTHROPIC_API_KEY is set, so Claude Code would bill the key, not your login.")
        log("  PowerShell:  Remove-Item Env:ANTHROPIC_API_KEY   (or open a new terminal)")
        return 2

    from claude_agent_sdk import ClaudeSDKError
    from shared.net import use_os_trust_store
    use_os_trust_store()
    try:
        asyncio.run(run_async(args.scenario, args.out, args.data_dir))
    except ClaudeSDKError as e:
        log(f"Claude Agent SDK error: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
