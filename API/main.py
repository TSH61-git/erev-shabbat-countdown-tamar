from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Run as "python API/main.py": Python puts API/ on the path, not the project root.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from API.agent import DEFAULT_COMPACT_THRESHOLD, ApiAgent  # noqa: E402
from shared import config, hebcal  # noqa: E402
from shared.checks import run_checks  # noqa: E402
from shared.results import api_loc, build_results, library_versions, write_results  # noqa: E402
from shared.scenario import load_scenario, reset_state  # noqa: E402


def log(msg: str) -> None:
    print(f"[main.py] {msg}", flush=True)


def run(scenario_path: str, out_path: str, client: object, data_dir: str | None = None,
        fetch: hebcal.Fetch = hebcal.fetch_json,
        compact_threshold: int = DEFAULT_COMPACT_THRESHOLD) -> dict:
    scenario = load_scenario(scenario_path)
    store = reset_state(config.data_dir("api", data_dir))
    log(f"clean state in {store.root}; {len(scenario.turns)} turns in {len(scenario.sessions)} sessions")
    records = []
    for turns in scenario.sessions:
        agent = ApiAgent(client, store, turns[0].session, fetch, compact_threshold)
        for turn in turns:
            record = agent.ask(turn)
            log(f"turn {turn.turn} reply: {record.reply_preview[:120]!r}")
            records.append(record)
    checks = run_checks(store, scenario.expect)
    for c in checks:
        log(f"check {c.id} {'PASS' if c.passed else 'FAIL'}: {c.name} ({c.detail})")
    results = build_results(
        "api", records, checks, api_loc(ROOT / "API" / "harness"),
        library_versions(["anthropic", "requests", "truststore"]),
        {"compact_threshold": compact_threshold,
         "compacted_turns": [r.turn for r in records if r.compacted]})
    write_results(out_path, results)
    t = results["totals"]
    log(f"wrote {out_path}: checks {results['checks_passed']}, {t['model_calls']} model calls, "
        f"cache read {t['cache_read_pct']}%, ${t['cost_usd']:.4f}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Erev Shabbat Countdown on the raw Messages API")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--compact-threshold", type=int, default=DEFAULT_COMPACT_THRESHOLD)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY is not set. Set it in this terminal only, never in a file:")
        log('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        return 2

    from shared.net import use_os_trust_store
    use_os_trust_store()
    import anthropic
    client = anthropic.Anthropic(max_retries=3)
    run(args.scenario, args.out, client, args.data_dir, compact_threshold=args.compact_threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
