from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from shared import memory_files as mem
from shared.domain import parse_hhmm
from shared.store import DataStore
from shared.tools_impl import LAST_TIMELINE, TIMELINES, TOOL_LOG


@dataclass
class CheckResult:
    id: int
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_profile(store: DataStore, ex: dict) -> tuple[bool, str]:
    p = mem.load_profile(store)
    if p is None:
        return False, "no profile saved"
    ok = p.city == ex["city"] and p.ready_buffer_min == ex["ready_buffer_min"]
    return ok, f"{p.city}/{p.ready_buffer_min}"


def check_checklist(store: DataStore, ex: dict) -> tuple[bool, str]:
    items = mem.load_checklist(store)
    texts = [i.text.casefold() for i in items]
    missing = [k for k in ex["checklist_keywords"] if not any(k in t for t in texts)]
    minutes = sum(i.minutes for i in items)
    last = store.read_json(LAST_TIMELINE)
    final = last["blocks"][-1] if last and last["blocks"] else None
    is_last = bool(final and final["kind"] == "checklist" and final["end"] == last["ready"])
    ok = not missing and len(items) == len(ex["checklist_keywords"]) \
        and minutes == ex["checklist_minutes"] and is_last
    return ok, (f"{len(items)} items, {minutes} min, missing {missing or 'none'}, "
                f"last block is checklist: {is_last}")


def check_learned(store: DataStore, ex: dict) -> tuple[bool, str]:
    learned = mem.learned_minutes(store)
    got = {k: learned.get(k) for k in ex["learned"]}
    return got == ex["learned"], f"learned {got}"


def check_session2_uses_learned(store: DataStore, ex: dict) -> tuple[bool, str]:
    timelines = [t for t in store.read_jsonl(TIMELINES) if t["session"] == 2]
    if not timelines:
        return False, "no timeline built in session 2"
    wrong = [(t["turn"], name, t["planned_minutes"].get(name))
             for t in timelines for name, want in ex["session2_minutes"].items()
             if name in t["planned_minutes"] and t["planned_minutes"][name] != want]
    return not wrong, f"{len(timelines)} session-2 timelines, wrong: {wrong or 'none'}"


def check_week_overrides(store: DataStore, ex: dict) -> tuple[bool, str]:
    weeks = [w for w in mem.list_weeks(store) if mem.load_week(store, w)]
    if weeks != [ex["override_friday"]]:
        return False, f"weeks with overrides: {weeks}"
    overrides = mem.load_week(store, ex["override_friday"])
    templates = {t.name: t for t in mem.load_tasks(store)}
    moved = {n for o in overrides if o.type == "move_to_thursday" for n in o.tasks}
    skipped = {n for o in overrides if o.type == "skip_task" for n in [o.task]}
    skip_tags = {tag for o in overrides if o.type == "skip_tags" for tag in o.tags}
    skipped |= {n for n, t in templates.items() if skip_tags & set(t.tags)}
    has_move = set(ex["thursday_tasks"]) <= moved
    has_skip = set(ex["dinner_tasks"]) <= skipped
    unchanged = {n: templates[n].minutes if n in templates else None
                 for n in ex["templates_unchanged"]}
    ok = has_move and has_skip and unchanged == ex["templates_unchanged"]
    return ok, f"move: {has_move}, dinner skip: {has_skip}, templates {unchanged}"


def timeline_violations(tl: dict, buffer: int) -> list[str]:
    out = []
    ready = parse_hhmm(tl["ready"])
    if ready != parse_hhmm(tl["candles"]) - buffer:
        out.append(f"ready {tl['ready']} is not candles {tl['candles']} minus {buffer}")
    if not tl["fits"]:
        out.append(f"plan does not fit (over by {tl['overflow']})")
    blocks = [(parse_hhmm(b["start"]), parse_hhmm(b["end"]), b) for b in tl["blocks"]]
    for s, e, b in blocks:
        if e > ready:
            out.append(f"{b['task'] or b['kind']} ends {b['end']} after ready {tl['ready']}")
    mine = sorted((s, e, b) for s, e, b in blocks if b["kind"] in ("active", "checklist", "break"))
    for (s1, e1, b1), (s2, e2, b2) in zip(mine, mine[1:]):
        if s2 < e1:
            out.append(f"{b1['task'] or b1['kind']} overlaps {b2['task'] or b2['kind']}")
    spans = {b["task"]: (s, e) for s, e, b in blocks if b["task"]}
    for name, (s, _) in spans.items():
        for dep in tl["depends_on"].get(name, []):
            if dep in spans and spans[dep][1] > s:
                out.append(f"{name} starts before {dep} ends")
    return out


def check_final_timeline(store: DataStore, ex: dict) -> tuple[bool, str]:
    tl = store.read_json(LAST_TIMELINE)
    if tl is None:
        return False, "no timeline built"
    problems = timeline_violations(tl, ex["ready_buffer_min"])
    return not problems, f"turn {tl['turn']}: " + ("; ".join(problems) or "valid")


def check_external_tool(store: DataStore, ex: dict) -> tuple[bool, str]:
    calls = [c for c in store.read_jsonl(TOOL_LOG) if c["tool"] == ex["external_tool"] and c["ok"]]
    return bool(calls), f"{len(calls)} successful {ex['external_tool']} calls"


CHECKS: list[tuple[int, str, Callable[[DataStore, dict], tuple[bool, str]]]] = [
    (1, "city and buffer saved", check_profile),
    (2, "checklist saved and last in timeline", check_checklist),
    (3, "learned challah duration", check_learned),
    (4, "session 2 uses learned duration", check_session2_uses_learned),
    (5, "week overrides only for their Friday, templates unchanged", check_week_overrides),
    (6, "final timeline valid", check_final_timeline),
    (7, "external tool called", check_external_tool),
]


def run_checks(store: DataStore, expect: dict) -> list[CheckResult]:
    results = []
    for cid, name, fn in CHECKS:
        # A broken state must fail its check, not crash the run and lose the results file.
        try:
            passed, detail = fn(store, expect)
        except Exception as e:
            passed, detail = False, f"error: {type(e).__name__}: {e}"
        results.append(CheckResult(cid, name, passed, detail))
    return results
