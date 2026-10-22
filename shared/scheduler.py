from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime

from shared.domain import (ChecklistItem, Profile, Task, ToolError, WeekOverride,
                           fmt_hhmm, parse_hhmm)

DAY = 24 * 60


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    kind: str
    task: str | None = None
    label: str = ""
    last: bool = False
    optional: bool = False


@dataclass
class Suggestion:
    tasks: list[str]
    active_minutes: int
    blocks: list[Block]
    remaining_overflow: int


@dataclass
class Timeline:
    friday: date
    candles: int
    ready: int
    day_start: int
    blocks: list[Block]
    planned: dict[str, Task]
    overflow: int = 0
    slack: int = 0
    thursday_blocks: list[Block] = field(default_factory=list)
    thursday_overflow: int = 0
    suggestion: Suggestion | None = None
    dropped: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    replan_from: int | None = None
    delay: int = 0

    @property
    def fits(self) -> bool:
        return self.overflow == 0

    def to_dict(self) -> dict:
        def blocks(bs):
            return [{"start": fmt_hhmm(b.start), "end": fmt_hhmm(b.end), "kind": b.kind,
                     "task": b.task, "last": b.last, "optional": b.optional} for b in bs]
        return {
            "friday": self.friday.isoformat(), "candles": fmt_hhmm(self.candles),
            "ready": fmt_hhmm(self.ready), "day_start": fmt_hhmm(self.day_start),
            "fits": self.fits, "overflow": self.overflow, "slack": self.slack,
            "blocks": blocks(self.blocks), "thursday_blocks": blocks(self.thursday_blocks),
            "planned_minutes": {n: t.minutes for n, t in sorted(self.planned.items())},
            "depends_on": {n: list(t.depends_on) for n, t in sorted(self.planned.items())},
            "suggestion": None if not self.suggestion else {
                "tasks": self.suggestion.tasks, "active_minutes": self.suggestion.active_minutes,
                "remaining_overflow": self.suggestion.remaining_overflow},
            "dropped": self.dropped, "ignored": self.ignored, "notes": self.notes,
            "replan_from": None if self.replan_from is None else fmt_hhmm(self.replan_from),
            "delay": self.delay,
        }


def label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _passive_closure(names: set[str], tasks: dict[str, Task]) -> set[str]:
    # Rising and baking cannot happen without the dough, so they follow it
    # when it is skipped, moved or dropped.
    out = set(names)
    grew = True
    while grew:
        grew = False
        for t in tasks.values():
            if t.name not in out and not t.active and out & set(t.depends_on):
                out.add(t.name)
                grew = True
    return out


def _strip_deps(tasks: dict[str, Task]) -> dict[str, Task]:
    return {n: replace(t, depends_on=tuple(d for d in t.depends_on if d in tasks))
            for n, t in tasks.items()}


@dataclass
class _Week:
    friday: dict[str, Task]
    thursday: dict[str, Task]
    day_start: int | None
    ignored: list[str]
    notes: list[str]


def effective_week(templates: list[Task], learned: dict[str, int],
                   overrides: list[WeekOverride]) -> _Week:
    tasks = {t.name: replace(t, minutes=learned.get(t.name, t.minutes)) for t in templates}
    day_start = None
    skipped: set[str] = set()
    moved: set[str] = set()
    ignored: list[str] = []
    notes: list[str] = []
    for o in overrides:
        if o.type == "start_time":
            day_start = parse_hhmm(o.time)
        elif o.type == "skip_tags":
            hit = {n for n, t in tasks.items() if set(t.tags) & set(o.tags)}
            if not hit:
                ignored.append(f"skip_tags {','.join(o.tags)} (no task has these tags)")
            skipped |= hit
        elif o.type == "add_task":
            if o.new_task.name in tasks:
                ignored.append(f"add_task {o.new_task.name} (already a task)")
            else:
                tasks[o.new_task.name] = o.new_task
        else:
            # A task removed with remove_task after the override was saved: the
            # override is stale, not an error the user must fix before planning.
            names = [o.task] if o.task else list(o.tasks)
            for n in names:
                if n not in tasks:
                    ignored.append(f"{o.type} {n} (task no longer exists)")
            names = [n for n in names if n in tasks]
            if o.type == "skip_task":
                skipped |= set(names)
            elif o.type == "set_minutes" and names:
                tasks[o.task] = replace(tasks[o.task], minutes=o.minutes)
            elif o.type == "move_to_thursday":
                moved |= set(names)

    skipped = _passive_closure(skipped, tasks)
    moved = _passive_closure(moved, tasks) - skipped
    for n in sorted(moved):
        early = sorted(d for d in tasks[n].depends_on if d not in moved and d not in skipped)
        if early:
            notes.append(f"{n} moved to Thursday before {', '.join(early)}")
    keep = {n: t for n, t in tasks.items() if n not in skipped}
    friday = _strip_deps({n: t for n, t in keep.items() if n not in moved})
    thursday = _strip_deps({n: t for n, t in keep.items() if n in moved})
    return _Week(friday, thursday, day_start, ignored, notes)


@dataclass
class _Plan:
    blocks: list[Block]
    overflow: int
    slack: int
    latest_start: dict[str, int]


def _last_task(tasks: dict[str, Task]) -> Task | None:
    return next((t for t in tasks.values() if t.anchor and t.anchor.type == "last"), None)


def _end_by(t: Task) -> int:
    return parse_hhmm(t.anchor.time) if t.anchor and t.anchor.type == "end_by" else DAY


def _backward(tasks: dict[str, Task], last: Task | None, active_limit: int,
              ready: int) -> dict[str, int]:
    succ: dict[str, list[str]] = {n: [] for n in tasks}
    for t in tasks.values():
        for d in t.depends_on:
            succ[d].append(t.name)
    ls: dict[str, int] = {}
    if last:
        ls[last.name] = active_limit
    todo = {n for n in tasks if n not in ls}
    cursor = active_limit
    while todo:
        eligible = sorted(n for n in todo if all(s in ls for s in succ[n]))
        passive = [n for n in eligible if not tasks[n].active]
        if passive:
            for n in passive:
                limit = min([ready, _end_by(tasks[n])] + [ls[s] for s in succ[n]])
                ls[n] = limit - tasks[n].minutes
                todo.remove(n)
            continue

        def limit(n: str) -> int:
            return min([cursor, _end_by(tasks[n])] + [ls[s] for s in succ[n]])

        # Ties go to non-heavy tasks: placed later here, so heavy work lands earlier in the day.
        n = max(eligible, key=lambda n: (limit(n), not tasks[n].heavy, n))
        ls[n] = limit(n) - tasks[n].minutes
        cursor = ls[n]
        todo.remove(n)
    return ls


def _forward(tasks: dict[str, Task], last: Task | None, ls: dict[str, int],
             day_start: int, active_limit: int, ready: int) -> dict[str, int] | None:
    start: dict[str, int] = {}
    end: dict[str, int] = {}
    passive_left = sorted(n for n, t in tasks.items() if not t.active)
    pending = {n for n, t in tasks.items() if t.active and (not last or n != last.name)}

    def deps_end(n: str) -> int:
        return max([day_start] + [end[d] for d in tasks[n].depends_on])

    def place_passives() -> None:
        for n in list(passive_left):
            if all(d in end for d in tasks[n].depends_on):
                start[n] = deps_end(n)
                end[n] = start[n] + tasks[n].minutes
                passive_left.remove(n)
                place_passives()
                return

    place_passives()
    cursor = day_start
    while pending:
        avail = [n for n in pending if all(d in end for d in tasks[n].depends_on)]
        if not avail:
            return None
        now_ready = [n for n in avail if deps_end(n) <= cursor]
        if not now_ready:
            cursor = min(deps_end(n) for n in avail)
            continue
        n = min(now_ready, key=lambda n: (ls[n], n))
        start[n], end[n] = cursor, cursor + tasks[n].minutes
        cursor = end[n]
        pending.remove(n)
        place_passives()
    if last:
        start[last.name], end[last.name] = active_limit, active_limit + last.minutes
        place_passives()
    if passive_left:
        return None
    for n, t in tasks.items():
        limit = ready if not t.active else active_limit + (t.minutes if t is last else 0)
        if end[n] > min(limit, _end_by(t)) or start[n] < deps_end(n):
            return None
    return start


def _plan_friday(tasks: dict[str, Task], day_start: int, ready: int,
                 checklist: list[ChecklistItem]) -> _Plan:
    c_min = sum(i.minutes for i in checklist)
    blocks: list[Block] = []
    if checklist:
        blocks.append(Block(ready - c_min, ready, "checklist",
                            label="Checklist: " + ", ".join(i.text for i in checklist)))
    last = _last_task(tasks)
    active_limit = ready - c_min - (last.minutes if last else 0)
    ls = _backward(tasks, last, active_limit, ready)
    overflow = max(0, day_start - min(ls.values(), default=day_start))
    starts = _forward(tasks, last, ls, day_start, active_limit, ready) if not overflow else None
    # Forward placement keeps the slack where a live re-plan can use it; the
    # backward placement is the fallback that is always valid when it fits.
    starts = starts or ls
    for n, t in tasks.items():
        blocks.append(Block(starts[n], starts[n] + t.minutes, t.kind, n, label(n),
                            last=t is last))
    busy_end = max([day_start] + [starts[n] + t.minutes for n, t in tasks.items()
                                  if t.active and t is not last])
    slack = max(0, active_limit - busy_end) if not overflow else 0
    if slack >= 15:
        blocks.append(Block(busy_end, busy_end + 10, "break", label="Short break", optional=True))
        if slack > 10:
            blocks.append(Block(busy_end + 10, active_limit, "free", label="free"))
    elif slack:
        blocks.append(Block(busy_end, active_limit, "free", label="free"))
    return _Plan(sorted(blocks, key=_order), overflow, slack, ls)


def _order(b: Block) -> tuple:
    return (b.start, b.kind != "active", b.task or "")


def _plan_thursday(tasks: dict[str, Task], start: int, end: int) -> tuple[list[Block], int]:
    blocks: list[Block] = []
    done: dict[str, int] = {}
    cursor = start
    left = sorted(tasks)
    while left:
        n = next(n for n in left if all(d in done for d in tasks[n].depends_on))
        t = tasks[n]
        s = max([start] + [done[d] for d in t.depends_on])
        if t.active:
            s = max(s, cursor)
            cursor = s + t.minutes
        done[n] = s + t.minutes
        blocks.append(Block(s, s + t.minutes, t.kind, n, label(n)))
        left.remove(n)
    return sorted(blocks, key=_order), max(0, max(done.values(), default=start) - end)


def _suggest_thursday(tasks: dict[str, Task], day_start: int, ready: int,
                      checklist: list[ChecklistItem], window: int) -> tuple[list[str], _Plan] | None:
    groups = []
    for t in sorted(tasks.values(), key=lambda t: t.name):
        if not (t.active and t.thursday_ok and t.anchor is None):
            continue
        group = _passive_closure({t.name}, tasks)
        if any(d not in group for g in group for d in tasks[g].depends_on):
            continue
        active_min = sum(tasks[g].minutes for g in group if tasks[g].active)
        if active_min <= window:
            groups.append((group, active_min, t.heavy, t.name))

    def without(names: set[str]) -> _Plan:
        return _plan_friday(_strip_deps({n: t for n, t in tasks.items() if n not in names}),
                            day_start, ready, checklist)

    first = _plan_friday(tasks, day_start, ready, checklist)
    singles = sorted(groups, key=lambda g: (g[1] < first.overflow, not g[2], g[1], g[3]))
    for group, _, _, _ in singles:
        plan = without(group)
        if plan.overflow == 0:
            return sorted(group), plan
    chosen: set[str] = set()
    for group, _, _, _ in sorted(groups, key=lambda g: (-g[1], g[3])):
        if sum(tasks[n].minutes for n in chosen | group if tasks[n].active) > window:
            continue
        chosen |= group
        plan = without(chosen)
        if plan.overflow == 0:
            return sorted(chosen), plan
    return (sorted(chosen), without(chosen)) if chosen else None


def build_timeline(*, friday: date, candles: str, profile: Profile, templates: list[Task],
                   learned: dict[str, int], checklist: list[ChecklistItem],
                   overrides: list[WeekOverride], now: datetime | None = None,
                   delay: int = 0) -> Timeline:
    candles_min = parse_hhmm(candles, "candles")
    ready = candles_min - profile.ready_buffer_min
    week = effective_week(templates, learned, overrides)
    day_start = week.day_start if week.day_start is not None else parse_hhmm(profile.friday_start)
    thu_start = parse_hhmm(profile.thursday_night_start)
    thu_end = parse_hhmm(profile.thursday_night_end)
    if thu_end <= thu_start:
        thu_end += DAY
    planned = {**week.friday, **week.thursday}

    if now is not None:
        return _replan(friday, candles_min, ready, day_start, week, planned, checklist, now, delay)

    plan = _plan_friday(week.friday, day_start, ready, checklist)
    thu_blocks, thu_over = _plan_thursday(week.thursday, thu_start, thu_end)
    tl = Timeline(friday, candles_min, ready, day_start, plan.blocks, planned,
                  overflow=plan.overflow, slack=plan.slack, thursday_blocks=thu_blocks,
                  thursday_overflow=thu_over, ignored=week.ignored, notes=week.notes)
    if plan.overflow:
        window = thu_end - thu_start - sum(t.minutes for t in week.thursday.values() if t.active)
        found = _suggest_thursday(week.friday, day_start, ready, checklist, window)
        if found:
            names, after = found
            moved = {n: week.friday[n] for n in names}
            sug_blocks, _ = _plan_thursday({**week.thursday, **moved}, thu_start, thu_end)
            sug_blocks = [b for b in sug_blocks if b.task in moved]
            tl.suggestion = Suggestion(names, sum(t.minutes for t in moved.values() if t.active),
                                       sug_blocks, after.overflow)
            tl.blocks, tl.slack = after.blocks, after.slack
    return tl


def _replan(friday: date, candles: int, ready: int, day_start: int, week: _Week,
            planned: dict[str, Task], checklist: list[ChecklistItem],
            now: datetime, delay: int) -> Timeline:
    if now.date() != friday:
        raise ToolError(f"re-plan needs the Friday itself ({friday}), now is {now.date()}")
    if delay < 0 or delay > 600:
        raise ToolError(f"delay_minutes must be 0-600, got {delay}")
    now_min = now.hour * 60 + now.minute
    if now_min >= ready:
        raise ToolError(f"it is already past the ready time {fmt_hhmm(ready)}")
    base = _plan_friday(week.friday, day_start, ready, checklist)
    effective = now_min - delay
    remaining: dict[str, Task] = {}
    for b in base.blocks:
        if b.task is None or b.end <= effective:
            continue
        t = week.friday[b.task]
        remaining[b.task] = replace(t, minutes=b.end - effective) if b.start < effective else t
    remaining = _strip_deps(remaining)
    start = max(now_min, day_start)
    dropped: list[str] = []
    plan = _plan_friday(remaining, start, ready, checklist)
    while plan.overflow:
        nice = [n for n, t in remaining.items() if t.priority == "nice"]
        if not nice:
            break
        victim = max(nice, key=lambda n: (plan.latest_start[n], remaining[n].minutes, n))
        gone = _passive_closure({victim}, remaining)
        dropped += sorted(gone)
        remaining = _strip_deps({n: t for n, t in remaining.items() if n not in gone})
        plan = _plan_friday(remaining, start, ready, checklist)
    return Timeline(friday, candles, ready, start, plan.blocks, planned,
                    overflow=plan.overflow, slack=plan.slack, dropped=dropped,
                    ignored=week.ignored, notes=week.notes, replan_from=now_min, delay=delay)


def _line(b: Block, prefix: str = "") -> str:
    if b.kind == "free":
        return f"{prefix}{fmt_hhmm(b.start)}-{fmt_hhmm(b.end)} free"
    if b.kind == "checklist":
        return f"{prefix}{fmt_hhmm(b.start)}-{fmt_hhmm(b.end)} {b.label}"
    tags = [b.kind] if b.kind in ("active", "passive") else []
    tags += ["last"] if b.last else []
    tags += ["optional"] if b.optional else []
    return f"{prefix}{fmt_hhmm(b.start)}-{fmt_hhmm(b.end)} {b.label} ({', '.join(tags)})"


def render(tl: Timeline) -> str:
    head = (f"Fri {tl.friday} candles {fmt_hhmm(tl.candles)} ready {fmt_hhmm(tl.ready)}"
            f" | start {fmt_hhmm(tl.day_start)} | slack {tl.slack}")
    lines = [head]
    if tl.replan_from is not None:
        lines.append(f"RE-PLAN from {fmt_hhmm(tl.replan_from)} ({tl.delay} behind)"
                     + (f": dropped {', '.join(tl.dropped)} (nice)" if tl.dropped else ""))
    if tl.overflow and tl.suggestion and tl.suggestion.remaining_overflow == 0:
        moves = ", ".join(f"{n} ({tl.planned[n].minutes})" for n in tl.suggestion.tasks
                          if tl.planned[n].active)
        span = f"{fmt_hhmm(tl.suggestion.blocks[0].start)}-{fmt_hhmm(tl.suggestion.blocks[-1].end)}"
        lines.append(f"DOES NOT FIT by {tl.overflow} min. SUGGEST Thursday night {span}: "
                     f"{moves}. Ask user to approve. Plan below assumes it.")
    elif tl.overflow:
        musts = sorted(n for b in tl.blocks if b.task and tl.planned.get(b.task)
                       and tl.planned[b.task].priority == "must" for n in [b.task])
        lines.append(f"DOES NOT FIT by {tl.overflow} min even after dropping nice tasks "
                     f"(would need to start {fmt_hhmm(tl.day_start - tl.overflow)}). "
                     f"MUST tasks: {', '.join(musts)}")
    if tl.thursday_blocks:
        lines += [_line(b, "Thu ") for b in tl.thursday_blocks]
        if tl.thursday_overflow:
            lines.append(f"Thursday night over by {tl.thursday_overflow} min")
    lines += [_line(b) for b in tl.blocks]
    lines += [f"ignored override: {x}" for x in tl.ignored]
    lines += [f"note: {x}" for x in tl.notes]
    return "\n".join(lines)


def find_violations(tl: Timeline) -> list[str]:
    out: list[str] = []
    tasks = {b.task: b for b in tl.blocks if b.task}
    for b in tl.blocks:
        if b.end > tl.ready:
            out.append(f"{b.label or b.kind} ends {fmt_hhmm(b.end)} after ready {fmt_hhmm(tl.ready)}")
    checklist = [b for b in tl.blocks if b.kind == "checklist"]
    if checklist and (checklist[0].end != tl.ready
                      or any(b.end > checklist[0].start for b in tl.blocks
                             if b.kind in ("active", "break"))):
        out.append("checklist is not the final block before ready")
    me = sorted((b for b in tl.blocks if b.kind in ("active", "checklist", "break")),
                key=lambda b: b.start)
    for a, b in zip(me, me[1:]):
        if b.start < a.end:
            out.append(f"{a.label} and {b.label} overlap")
    for n, b in tasks.items():
        for d in tl.planned[n].depends_on:
            if d in tasks and tasks[d].end > b.start:
                out.append(f"{n} starts before {d} ends")
        if _end_by(tl.planned[n]) < b.end:
            out.append(f"{n} ends after its {tl.planned[n].anchor.time} anchor")
    return out
