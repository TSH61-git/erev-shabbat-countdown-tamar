from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from shared import hebcal, memory_files as mem, scheduler
from shared.domain import Profile, ToolError, WeekOverride, fmt_hhmm, parse_friday, parse_hhmm
from shared.store import DataStore
from shared.tool_specs import TOOL_SPECS

TOOL_LOG = "tool_log.jsonl"
LAST_TIMELINE = "last_timeline.json"
TIMELINES = "timelines.jsonl"


@dataclass
class ToolContext:
    store: DataStore
    now: datetime
    turn: int = 0
    session: int = 0
    fetch: hebcal.Fetch = hebcal.fetch_json


def _profile(ctx: ToolContext) -> Profile:
    profile = mem.load_profile(ctx.store)
    if profile is None:
        raise ToolError("no profile yet: call update_profile with city and ready_buffer_min first")
    return profile


def get_shabbat_times(ctx: ToolContext, friday_date: str) -> str:
    profile = _profile(ctx)
    t, _ = hebcal.get_shabbat_times(ctx.store, profile.geonameid, friday_date, ctx.fetch)
    ready = fmt_hhmm(parse_hhmm(t.candles) - profile.ready_buffer_min)
    parasha = f" ({t.parasha})" if t.parasha else ""
    return (f"{t.friday} {profile.city}: candles {t.candles}, havdalah {t.havdalah or '?'}"
            f"{parasha}. ready {ready}")


def update_profile(ctx: ToolContext, **fields: object) -> str:
    p = mem.update_profile(ctx.store, **fields)
    return (f"profile: {p.city}, buffer {p.ready_buffer_min}, Fri start {p.friday_start}, "
            f"Thu night {p.thursday_night_start}-{p.thursday_night_end}")


def upsert_tasks(ctx: ToolContext, tasks: list) -> str:
    added, updated = mem.upsert_tasks(ctx.store, tasks)
    total = len(mem.load_tasks(ctx.store))
    parts = [f"added {len(added)}"] + ([f"updated {', '.join(updated)}"] if updated else [])
    return f"saved: {'; '.join(parts)}. {total} tasks total"


def _task_line(t, learned: dict[str, int]) -> str:
    bits = [t.name, f"{learned[t.name]}m*" if t.name in learned else f"{t.minutes}m",
            t.kind, t.priority]
    if t.depends_on:
        bits.append("<-" + ",".join(t.depends_on))
    if t.anchor:
        bits.append("last" if t.anchor.type == "last" else f"by {t.anchor.time}")
    bits += ["heavy"] * t.heavy + ["thu_ok"] * t.thursday_ok + [f"#{x}" for x in t.tags]
    return " ".join(bits)


def list_tasks(ctx: ToolContext) -> str:
    tasks = mem.load_tasks(ctx.store)
    learned = mem.learned_minutes(ctx.store)
    items = mem.load_checklist(ctx.store)
    lines = [_task_line(t, learned) for t in tasks] or ["no tasks saved"]
    if items:
        lines.append(f"checklist ({sum(i.minutes for i in items)} min): "
                     + ", ".join(f"{i.text} {i.minutes}" for i in items))
    return "\n".join(lines)


def remove_task(ctx: ToolContext, name: str) -> str:
    changed = mem.remove_task(ctx.store, name)
    total = len(mem.load_tasks(ctx.store))
    deps = ", ".join(changed) if changed else "none"
    return f"removed template {name} permanently; dependency removed from: {deps}. {total} tasks"


def _checklist_summary(ctx: ToolContext) -> str:
    items = mem.load_checklist(ctx.store)
    return f"{len(items)} items, {sum(i.minutes for i in items)} min"


def set_checklist(ctx: ToolContext, items: list, mode: str = "replace") -> str:
    mem.set_checklist(ctx.store, items, mode)
    return f"checklist: {_checklist_summary(ctx)}"


def remove_checklist_item(ctx: ToolContext, text: str) -> str:
    item = mem.remove_checklist_item(ctx.store, text)
    return f'removed checklist item "{item.text}" permanently. {_checklist_summary(ctx)}'


def log_actuals(ctx: ToolContext, friday_date: str, entries: list) -> str:
    changes = mem.log_actuals(ctx.store, friday_date, entries)
    return "learned: " + ", ".join(f"{n} {old}->{new}" for n, old, new in changes)


def describe_override(o: WeekOverride) -> str:
    return {
        "skip_task": lambda: f"skip {o.task}",
        "skip_tags": lambda: f"skip tag {','.join(o.tags)}",
        "set_minutes": lambda: f"{o.task} takes {o.minutes} min",
        "start_time": lambda: f"start at {o.time}",
        "move_to_thursday": lambda: f"move {', '.join(o.tasks)} to Thursday night",
        "add_task": lambda: f"add {o.new_task.name} ({o.new_task.minutes} min)",
    }[o.type]()


def set_week_override(ctx: ToolContext, friday_date: str, change: dict, note: str = "") -> str:
    friday = parse_friday(friday_date)
    o, created = mem.add_week_override(ctx.store, friday_date, change, note)
    if not created:
        return f"already saved for {friday}: {describe_override(o)}"
    templates = {t.name: t for t in mem.load_tasks(ctx.store)}
    week = scheduler.effective_week(list(templates.values()), {}, [o])
    line = f"override {friday}: {describe_override(o)}"
    skipped = sorted(set(templates) - set(week.friday) - set(week.thursday))
    if skipped:
        line += " -> skips " + ", ".join(f"{n}({templates[n].priority})" for n in skipped)
        if any(templates[n].priority == "must" for n in skipped):
            line += ". CONFLICT with saved tasks"
    if week.thursday:
        line += " -> Thursday night: " + ", ".join(sorted(week.thursday))
    return line + f". Templates unchanged; applies to {friday} only"


def _parse_now(value: str) -> datetime:
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"now must be YYYY-MM-DDTHH:MM, got {value!r}") from None
    return when.replace(tzinfo=None, second=0, microsecond=0)


def build_timeline(ctx: ToolContext, friday_date: str, now: str | None = None,
                   delay_minutes: int = 0) -> str:
    profile = _profile(ctx)
    friday = parse_friday(friday_date)
    templates = mem.load_tasks(ctx.store)
    if not templates:
        raise ToolError("no tasks saved yet: add them with upsert_tasks")
    t, _ = hebcal.get_shabbat_times(ctx.store, profile.geonameid, friday_date, ctx.fetch)
    when = _parse_now(now) if now else (ctx.now if delay_minutes else None)
    tl = scheduler.build_timeline(
        friday=friday, candles=t.candles, profile=profile, templates=templates,
        learned=mem.learned_minutes(ctx.store), checklist=mem.load_checklist(ctx.store),
        overrides=mem.active_overrides(ctx.store, friday_date, ctx.now),
        now=when, delay=delay_minutes)
    record = {**tl.to_dict(), "turn": ctx.turn, "session": ctx.session}
    ctx.store.write_json(LAST_TIMELINE, record)
    ctx.store.append_jsonl(TIMELINES, record)
    return scheduler.render(tl)


IMPLS: dict[str, Callable[..., str]] = {
    "get_shabbat_times": get_shabbat_times, "update_profile": update_profile,
    "upsert_tasks": upsert_tasks, "list_tasks": list_tasks, "remove_task": remove_task,
    "set_checklist": set_checklist, "remove_checklist_item": remove_checklist_item,
    "log_actuals": log_actuals, "set_week_override": set_week_override,
    "build_timeline": build_timeline,
}
_SCHEMAS = {s["name"]: s["input_schema"] for s in TOOL_SPECS}


def _check_args(name: str, args: object) -> dict:
    # Checked here instead of letting Python raise TypeError, which would also
    # hide real bugs inside the tool functions.
    if not isinstance(args, dict):
        raise ToolError(f"{name}: arguments must be an object")
    schema = _SCHEMAS[name]
    unknown = sorted(set(args) - set(schema["properties"]))
    missing = sorted(set(schema["required"]) - set(args))
    if unknown or missing:
        raise ToolError(f"{name}: " + "; ".join(
            ([f"unknown argument(s) {', '.join(unknown)}"] if unknown else [])
            + ([f"missing argument(s) {', '.join(missing)}"] if missing else [])))
    return args


def run_tool(ctx: ToolContext, name: str, args: object) -> str:
    entry = {"turn": ctx.turn, "session": ctx.session, "tool": name}
    try:
        if name not in IMPLS:
            raise ToolError(f"unknown tool {name}; tools: {', '.join(IMPLS)}")
        out = IMPLS[name](ctx, **_check_args(name, args))
    except ToolError as e:
        ctx.store.append_jsonl(TOOL_LOG, {**entry, "ok": False, "error": str(e)})
        raise
    ctx.store.append_jsonl(TOOL_LOG, {**entry, "ok": True, "chars": len(out)})
    return out
