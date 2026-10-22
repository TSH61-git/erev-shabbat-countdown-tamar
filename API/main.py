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


def preflight(client: object) -> int:
    from datetime import datetime

    from shared.scenario import Turn
    store = reset_state(config.DATA_ROOT / "api-preflight")
    agent = ApiAgent(client, store, session=1, compact_threshold=10**9)
    base = agent.base_request([{"role": "user", "content": "hi"}])
    counted = client.messages.count_tokens(
        model=base["model"], system=base["system"], tools=base["tools"],
        thinking=base["thinking"], messages=base["messages"]).input_tokens
    log(f"preflight: system + tools + 'hi' = {counted:,} tokens (Opus 5.5 caches from 512)")
    now = datetime(2026, 10, 29, 9, 0)
    for n, text in [(1, "Before we start: list my saved tasks."), (2, "Thanks, that's all for now.")]:
        record = agent.ask(Turn(1, n, now, "2026-10-30", text))
        log(f"preflight turn {n} reply: {record.reply_preview[:100]!r}")
    calls = agent.meter.calls
    cached = all(c.cache_read > 0 for c in calls[1:])
    log(f"preflight: {len(calls)} calls, cache read on every call after the first: {cached}; "
        f"history with thinking blocks replayed without error: True; "
        f"cost ${sum(c.cost for c in calls):.4f}")
    return 0 if counted >= 512 and cached else 1


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s: str) -> int:
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self) -> None:
        for st in self.streams:
            st.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Erev Shabbat Countdown on the raw Messages API")
    parser.add_argument("--scenario")
    parser.add_argument("--out")
    parser.add_argument("--data-dir")
    parser.add_argument("--compact-threshold", type=int, default=DEFAULT_COMPACT_THRESHOLD)
    parser.add_argument("--preflight", action="store_true",
                        help="3 small paid calls: prefix size, caching, history replay")
    parser.add_argument("--log", help="also write all output to this file (UTF-8)")
    args = parser.parse_args()
    if not args.preflight and not (args.scenario and args.out):
        parser.error("--scenario and --out are required (or use --preflight)")
    sys.stdout.reconfigure(encoding="utf-8")
    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = _Tee(sys.stdout, open(args.log, "w", encoding="utf-8"))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY is not set. Set it in this terminal only, never in a file:")
        log('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        return 2

    from shared.net import use_os_trust_store
    use_os_trust_store()
    import anthropic
    client = anthropic.Anthropic(max_retries=3)
    try:
        if args.preflight:
            return preflight(client)
        run(args.scenario, args.out, client, args.data_dir, compact_threshold=args.compact_threshold)
    except anthropic.APIStatusError as e:
        message = (e.body or {}).get("error", {}).get("message", str(e)) if isinstance(e.body, dict) else str(e)
        log(f"API error {e.status_code}: {message} (request_id {e.request_id})")
        return 1
    except anthropic.APIConnectionError as e:
        log(f"cannot reach the API: {e.__cause__ or e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
