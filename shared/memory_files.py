from __future__ import annotations

from datetime import date, datetime, time, timedelta

from shared import config
from shared.domain import (ChecklistItem, Profile, Task, ToolError, WeekOverride,
                           normalize_name, parse_friday)
from shared.store import DataStore

PROFILE = "profile.json"
TASKS = "tasks.json"
CHECKLIST = "checklist.json"
LEARNED = "learned.json"
WEEKS_DIR = "weeks"


def load_profile(store: DataStore) -> Profile | None:
    data = store.read_json(PROFILE)
    return Profile.from_dict(data) if data else None


def update_profile(store: DataStore, **changes: object) -> Profile:
    allowed = {"city", "ready_buffer_min", "friday_start",
               "thursday_night_start", "thursday_night_end"}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise ToolError(f"update_profile: unknown field(s) {', '.join(unknown)}")
    changes = {k: v for k, v in changes.items() if v is not None}
    current = load_profile(store)
    base = current.__dict__.copy() if current else {
        "friday_start": config.DEFAULT_FRIDAY_START,
        "thursday_night_start": config.DEFAULT_THURSDAY_NIGHT["start"],
        "thursday_night_end": config.DEFAULT_THURSDAY_NIGHT["end"],
    }
    base.pop("geonameid", None)
    base.update(changes)
    missing = [k for k in ("city", "ready_buffer_min") if k not in base]
    if missing:
        raise ToolError(f"first profile save needs: {', '.join(missing)}")
    profile = Profile.build(base["city"], base["ready_buffer_min"], base["friday_start"],
                            base["thursday_night_start"], base["thursday_night_end"])
    store.write_json(PROFILE, profile.to_dict())
    return profile


def load_tasks(store: DataStore) -> list[Task]:
    data = store.read_json(TASKS, {"tasks": []})
    return [Task.from_dict(t) for t in data["tasks"]]


def _save_tasks(store: DataStore, tasks: list[Task]) -> None:
    ordered = sorted(tasks, key=lambda t: t.name)
    store.write_json(TASKS, {"tasks": [t.to_dict() for t in ordered]})


def validate_task_set(tasks: list[Task]) -> None:
    names = {t.name for t in tasks}
    for t in tasks:
        missing = [d for d in t.depends_on if d not in names]
        if missing:
            raise ToolError(f"task {t.name} depends on unknown task(s): {', '.join(missing)}")
    last = sorted(t.name for t in tasks if t.anchor and t.anchor.type == "last")
    if len(last) > 1:
        raise ToolError(f"only one task can be last before the checklist, got: {', '.join(last)}")
    indegree = {t.name: len(t.depends_on) for t in tasks}
    dependents: dict[str, list[str]] = {t.name: [] for t in tasks}
    for t in tasks:
        for d in t.depends_on:
            dependents[d].append(t.name)
    ready = [n for n, k in indegree.items() if k == 0]
    seen = 0
    while ready:
        n = ready.pop()
        seen += 1
        for m in dependents[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                ready.append(m)
    if seen != len(tasks):
        cyclic = sorted(n for n, k in indegree.items() if k > 0)
        raise ToolError(f"dependency cycle between: {', '.join(cyclic)}")


def upsert_tasks(store: DataStore, raw_tasks: object) -> tuple[list[str], list[str]]:
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ToolError("tasks must be a non-empty list")
    incoming = [Task.from_dict(t) for t in raw_tasks]
    dupes = sorted({t.name for t in incoming if [x.name for x in incoming].count(t.name) > 1})
    if dupes:
        raise ToolError(f"task listed twice in one call: {', '.join(dupes)}")
    by_name = {t.name: t for t in load_tasks(store)}
    added = sorted(t.name for t in incoming if t.name not in by_name)
    updated = sorted(t.name for t in incoming if t.name in by_name)
    by_name.update({t.name: t for t in incoming})
    merged = list(by_name.values())
    # Validate the merged set before writing, so a bad batch leaves the file untouched.
    validate_task_set(merged)
    _save_tasks(store, merged)
    return added, updated


# Kept apart from week overrides: skipping a task for one week must never delete it.
def remove_task(store: DataStore, name: object) -> list[str]:
    target = normalize_name(name)
    tasks = load_tasks(store)
    if target not in {t.name for t in tasks}:
        known = ", ".join(sorted(t.name for t in tasks)) or "none"
        raise ToolError(f"no task named {target}; tasks: {known}")
    kept, stripped = [], []
    for t in tasks:
        if t.name == target:
            continue
        if target in t.depends_on:
            stripped.append(t.name)
            t = Task(**{**t.__dict__, "depends_on": tuple(d for d in t.depends_on if d != target)})
        kept.append(t)
    _save_tasks(store, kept)
    return sorted(stripped)


def load_checklist(store: DataStore) -> list[ChecklistItem]:
    data = store.read_json(CHECKLIST, {"items": []})
    return [ChecklistItem.from_dict(i) for i in data["items"]]


def _save_checklist(store: DataStore, items: list[ChecklistItem]) -> None:
    store.write_json(CHECKLIST, {"items": [i.to_dict() for i in items]})


def set_checklist(store: DataStore, raw_items: object, mode: str = "replace") -> list[ChecklistItem]:
    # No sorting: the user's order is the order the checklist is done on Friday.
    if mode not in ("replace", "append"):
        raise ToolError(f"mode must be replace|append, got {mode!r}")
    if not isinstance(raw_items, list) or not raw_items:
        raise ToolError("items must be a non-empty list")
    incoming = [ChecklistItem.from_dict(i) for i in raw_items]
    items = [] if mode == "replace" else load_checklist(store)
    for new in incoming:
        idx = next((k for k, old in enumerate(items)
                    if old.text.casefold() == new.text.casefold()), None)
        if idx is None:
            items.append(new)
        else:
            items[idx] = new
    if len(items) > 15:
        raise ToolError("checklist is limited to 15 items")
    _save_checklist(store, items)
    return items


def remove_checklist_item(store: DataStore, text: object) -> ChecklistItem:
    if not isinstance(text, str):
        raise ToolError(f"text must be a string, got {text!r}")
    items = load_checklist(store)
    for k, item in enumerate(items):
        if item.text.casefold() == text.strip().casefold():
            _save_checklist(store, items[:k] + items[k + 1:])
            return item
    known = "; ".join(i.text for i in items) or "none"
    raise ToolError(f"no checklist item {text!r}; items: {known}")


def load_learned(store: DataStore) -> dict[str, dict]:
    return store.read_json(LEARNED, {})


def learned_minutes(store: DataStore) -> dict[str, int]:
    return {name: rec["minutes"] for name, rec in load_learned(store).items()}


def _rounded_mean(values: list[int]) -> int:
    # Integer half-up: round() would turn 80.5 into 80 (banker's rounding).
    n = len(values)
    return (2 * sum(values) + n) // (2 * n)


def log_actuals(store: DataStore, friday_date: object,
                entries: object) -> list[tuple[str, int, int]]:
    # Templates stay as the user wrote them; the scheduler prefers learned minutes.
    # A correction for the same Friday replaces that sample instead of counting twice.
    friday = parse_friday(friday_date).isoformat()
    if not isinstance(entries, list) or not entries:
        raise ToolError("entries must be a non-empty list of {task, minutes}")
    templates = {t.name: t for t in load_tasks(store)}
    learned = load_learned(store)
    parsed = []
    for e in entries:
        if not isinstance(e, dict) or set(e) != {"task", "minutes"}:
            raise ToolError(f"each entry must be {{task, minutes}}, got {e!r}")
        name = normalize_name(e["task"])
        if name not in templates:
            raise ToolError(f"no task named {name}; tasks: {', '.join(sorted(templates))}")
        minutes = e["minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 600:
            raise ToolError(f"{name}: minutes must be an integer 1-600, got {minutes!r}")
        parsed.append((name, minutes))
    changes = []
    for name, minutes in parsed:
        rec = learned.get(name, {"samples": []})
        old = rec.get("minutes", templates[name].minutes)
        samples = [s for s in rec["samples"] if s["friday"] != friday]
        samples.append({"friday": friday, "minutes": minutes})
        samples.sort(key=lambda s: s["friday"])
        window = [s["minutes"] for s in samples[-config.LEARNED_WINDOW:]]
        learned[name] = {"minutes": _rounded_mean(window), "samples": samples}
        changes.append((name, old, learned[name]["minutes"]))
    store.write_json(LEARNED, learned)
    return changes


def week_file(friday: date) -> str:
    return f"{WEEKS_DIR}/{friday.isoformat()}.json"


def is_week_active(friday: date, now: datetime) -> bool:
    # Overrides are for that Shabbat only, so they end when Shabbat ends.
    return now < datetime.combine(friday + timedelta(days=2), time(0, 0))


def load_week(store: DataStore, friday_date: object) -> list[WeekOverride]:
    friday = parse_friday(friday_date)
    data = store.read_json(week_file(friday), {"overrides": []})
    return [WeekOverride.from_dict(o) for o in data["overrides"]]


def active_overrides(store: DataStore, friday_date: object, now: datetime) -> list[WeekOverride]:
    friday = parse_friday(friday_date)
    return load_week(store, friday_date) if is_week_active(friday, now) else []


def list_weeks(store: DataStore) -> list[str]:
    return store.list_json(WEEKS_DIR)


def add_week_override(store: DataStore, friday_date: object, change: object,
                      note: object = "") -> tuple[WeekOverride, bool]:
    friday = parse_friday(friday_date)
    if isinstance(change, dict) and note and "note" not in change:
        change = {**change, "note": note}
    override = WeekOverride.from_dict(change)
    templates = {t.name for t in load_tasks(store)}
    referenced = ([override.task] if override.task else []) + list(override.tasks)
    unknown = sorted(n for n in referenced if n not in templates)
    if unknown:
        raise ToolError(f"unknown task(s) {', '.join(unknown)}; tasks: {', '.join(sorted(templates))}")
    if override.new_task and override.new_task.name in templates:
        raise ToolError(f"add_task: {override.new_task.name} already exists as a template")
    existing = load_week(store, friday.isoformat())
    # The model may repeat a call; storing it twice would skip or move things twice.
    if override in existing:
        return override, False
    existing.append(override)
    store.write_json(week_file(friday), {"friday": friday.isoformat(),
                                         "overrides": [o.to_dict() for o in existing]})
    return override, True
