from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from shared import config


# Messages are written for the model: it gets them back as is_error results and retries.
class ToolError(ValueError):
    pass


_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_NAME = re.compile(r"^[a-z0-9_]{1,40}$")


def parse_hhmm(value: object, what: str = "time") -> int:
    if not isinstance(value, str) or not _HHMM.match(value):
        raise ToolError(f"{what} must be HH:MM (24h), got {value!r}")
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def fmt_hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(value: object, what: str = "date") -> date:
    if not isinstance(value, str):
        raise ToolError(f"{what} must be YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{what} must be YYYY-MM-DD, got {value!r}") from None


def parse_friday(value: object) -> date:
    d = parse_date(value, "friday_date")
    if d.weekday() != 4:
        raise ToolError(f"friday_date {value} is a {d.strftime('%A')}, not a Friday")
    return d


# The model may write "Set Table" or "set-table"; one spelling keeps dependencies,
# learned durations and overrides pointing at the same task.
def normalize_name(value: object, what: str = "task name") -> str:
    if not isinstance(value, str):
        raise ToolError(f"{what} must be a string, got {value!r}")
    name = re.sub(r"[\s\-]+", "_", value.strip().lower())
    if not _NAME.match(name):
        raise ToolError(f"{what} {value!r}: use letters, digits and _ only (max 40)")
    return name


def _int(value: object, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ToolError(f"{what} must be an integer {lo}-{hi}, got {value!r}")
    return value


def _bool(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise ToolError(f"{what} must be true or false, got {value!r}")
    return value


def _choice(value: object, what: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ToolError(f"{what} must be one of {'|'.join(choices)}, got {value!r}")
    return value  # type: ignore[return-value]


def _str_list(value: object, what: str) -> list:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ToolError(f"{what} must be a list of strings, got {value!r}")
    return value


def _unknown_keys(d: dict, allowed: set[str], what: str) -> None:
    extra = sorted(set(d) - allowed)
    if extra:
        raise ToolError(f"{what}: unknown field(s) {', '.join(extra)}")


@dataclass(frozen=True)
class Anchor:
    type: str
    time: str | None = None

    @staticmethod
    def from_dict(d: object) -> "Anchor | None":
        if d is None:
            return None
        if not isinstance(d, dict):
            raise ToolError(f"anchor must be null or an object, got {d!r}")
        kind = _choice(d.get("type"), "anchor.type", ("end_by", "last"))
        if kind == "last":
            _unknown_keys(d, {"type"}, "anchor")
            return Anchor("last")
        _unknown_keys(d, {"type", "time"}, "anchor")
        parse_hhmm(d.get("time"), "anchor.time")
        return Anchor("end_by", d["time"])

    def to_dict(self) -> dict:
        return {"type": "last"} if self.type == "last" else {"type": "end_by", "time": self.time}


TASK_FIELDS = {"name", "minutes", "kind", "depends_on", "priority", "anchor",
               "heavy", "tags", "thursday_ok"}


@dataclass(frozen=True)
class Task:
    name: str
    minutes: int
    kind: str = "active"
    depends_on: tuple[str, ...] = ()
    priority: str = "must"
    anchor: Anchor | None = None
    heavy: bool = False
    tags: tuple[str, ...] = ()
    thursday_ok: bool = False

    @property
    def active(self) -> bool:
        return self.kind == "active"

    @staticmethod
    def from_dict(d: object) -> "Task":
        if not isinstance(d, dict):
            raise ToolError(f"task must be an object, got {d!r}")
        name = normalize_name(d.get("name"))
        _unknown_keys(d, TASK_FIELDS, f"task {name}")
        minutes = _int(d.get("minutes"), f"{name}.minutes", 1, 600)
        kind = _choice(d.get("kind", "active"), f"{name}.kind", ("active", "passive"))
        deps = tuple(sorted({normalize_name(x, f"{name}.depends_on item")
                             for x in _str_list(d.get("depends_on", []), f"{name}.depends_on")}))
        if name in deps:
            raise ToolError(f"task {name} cannot depend on itself")
        priority = _choice(d.get("priority", "must"), f"{name}.priority", ("must", "nice"))
        anchor = Anchor.from_dict(d.get("anchor"))
        if anchor and anchor.type == "last" and kind == "passive":
            raise ToolError(f"task {name}: a passive task cannot be the last task")
        heavy = _bool(d.get("heavy", False), f"{name}.heavy")
        tags = tuple(sorted({normalize_name(x, f"{name}.tags item")
                             for x in _str_list(d.get("tags", []), f"{name}.tags")}))
        thursday_ok = _bool(d.get("thursday_ok", False), f"{name}.thursday_ok")
        return Task(name, minutes, kind, deps, priority, anchor, heavy, tags, thursday_ok)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "minutes": self.minutes, "kind": self.kind,
            "depends_on": list(self.depends_on), "priority": self.priority,
            "anchor": self.anchor.to_dict() if self.anchor else None,
            "heavy": self.heavy, "tags": list(self.tags), "thursday_ok": self.thursday_ok,
        }


@dataclass(frozen=True)
class ChecklistItem:
    text: str
    minutes: int

    @staticmethod
    def from_dict(d: object) -> "ChecklistItem":
        if not isinstance(d, dict):
            raise ToolError(f"checklist item must be an object, got {d!r}")
        _unknown_keys(d, {"text", "minutes"}, "checklist item")
        text = d.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 80:
            raise ToolError(f"checklist text must be 1-80 characters, got {text!r}")
        return ChecklistItem(text.strip(), _int(d.get("minutes"), "checklist minutes", 1, 30))

    def to_dict(self) -> dict:
        return {"text": self.text, "minutes": self.minutes}


@dataclass(frozen=True)
class Profile:
    city: str
    geonameid: int
    ready_buffer_min: int
    friday_start: str = config.DEFAULT_FRIDAY_START
    thursday_night_start: str = config.DEFAULT_THURSDAY_NIGHT["start"]
    thursday_night_end: str = config.DEFAULT_THURSDAY_NIGHT["end"]

    @staticmethod
    def build(city: object, ready_buffer_min: object, friday_start: object,
              thursday_night_start: object, thursday_night_end: object) -> "Profile":
        if not isinstance(city, str):
            raise ToolError(f"city must be a string, got {city!r}")
        canonical = config.canonical_city(city)
        if canonical is None:
            raise ToolError(f"city {city!r} not supported; supported: {', '.join(config.CITIES)}")
        buffer = _int(ready_buffer_min, "ready_buffer_min", 0, 60)
        parse_hhmm(friday_start, "friday_start")
        parse_hhmm(thursday_night_start, "thursday_night_start")
        parse_hhmm(thursday_night_end, "thursday_night_end")
        return Profile(canonical, config.CITIES[canonical], buffer, friday_start,  # type: ignore[arg-type]
                       thursday_night_start, thursday_night_end)  # type: ignore[arg-type]

    @staticmethod
    def from_dict(d: dict) -> "Profile":
        night = d.get("thursday_night", config.DEFAULT_THURSDAY_NIGHT)
        return Profile.build(d.get("city"), d.get("ready_buffer_min"),
                             d.get("friday_start", config.DEFAULT_FRIDAY_START),
                             night.get("start"), night.get("end"))

    def to_dict(self) -> dict:
        return {
            "city": self.city, "geonameid": self.geonameid,
            "ready_buffer_min": self.ready_buffer_min, "friday_start": self.friday_start,
            "thursday_night": {"start": self.thursday_night_start, "end": self.thursday_night_end},
        }


OVERRIDE_TYPES = ("skip_task", "skip_tags", "set_minutes", "start_time",
                  "move_to_thursday", "add_task")


@dataclass(frozen=True)
class WeekOverride:
    type: str
    task: str | None = None
    tags: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    minutes: int | None = None
    time: str | None = None
    new_task: Task | None = None
    # The same change with a different note is still a duplicate.
    note: str = field(default="", compare=False)

    @staticmethod
    def from_dict(d: object) -> "WeekOverride":
        if not isinstance(d, dict):
            raise ToolError(f"change must be an object, got {d!r}")
        kind = _choice(d.get("type"), "change.type", OVERRIDE_TYPES)
        note = d.get("note", "")
        if not isinstance(note, str) or len(note) > 120:
            raise ToolError("note must be a string of at most 120 characters")
        allowed = {
            "skip_task": {"task"}, "skip_tags": {"tags"}, "set_minutes": {"task", "minutes"},
            "start_time": {"time"}, "move_to_thursday": {"tasks"}, "add_task": {"new_task"},
        }[kind]
        _unknown_keys(d, allowed | {"type", "note"}, f"change {kind}")
        if kind == "skip_task":
            return WeekOverride(kind, task=normalize_name(d.get("task")), note=note)
        if kind == "set_minutes":
            return WeekOverride(kind, task=normalize_name(d.get("task")),
                                minutes=_int(d.get("minutes"), "minutes", 1, 600), note=note)
        if kind == "skip_tags":
            tags = tuple(sorted({normalize_name(t, "tag") for t in _str_list(d.get("tags"), "tags")}))
            if not tags:
                raise ToolError("skip_tags needs at least one tag")
            return WeekOverride(kind, tags=tags, note=note)
        if kind == "move_to_thursday":
            tasks = tuple(sorted({normalize_name(t) for t in _str_list(d.get("tasks"), "tasks")}))
            if not tasks:
                raise ToolError("move_to_thursday needs at least one task")
            return WeekOverride(kind, tasks=tasks, note=note)
        if kind == "start_time":
            parse_hhmm(d.get("time"), "start_time")
            return WeekOverride(kind, time=d["time"], note=note)
        return WeekOverride(kind, new_task=Task.from_dict(d.get("new_task")), note=note)

    def to_dict(self) -> dict:
        out: dict = {"type": self.type}
        if self.task is not None:
            out["task"] = self.task
        if self.tags:
            out["tags"] = list(self.tags)
        if self.tasks:
            out["tasks"] = list(self.tasks)
        if self.minutes is not None:
            out["minutes"] = self.minutes
        if self.time is not None:
            out["time"] = self.time
        if self.new_task is not None:
            out["new_task"] = self.new_task.to_dict()
        if self.note:
            out["note"] = self.note
        return out
