from __future__ import annotations

_DATE = {"type": "string", "description": "Friday date, YYYY-MM-DD"}
_HHMM = {"type": "string", "description": "24h time HH:MM"}

_TASK = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "snake_case, e.g. set_table"},
        "minutes": {"type": "integer", "minimum": 1, "maximum": 600},
        "kind": {"type": "string", "enum": ["active", "passive"],
                 "description": "active needs me; passive runs by itself (rising, oven, slow cooker)"},
        "depends_on": {"type": "array", "items": {"type": "string"},
                       "description": "tasks that must finish before this one starts"},
        "priority": {"type": "string", "enum": ["must", "nice"]},
        "anchor": {
            "type": ["object", "null"],
            "description": "null, {type: end_by, time: HH:MM}, or {type: last} = last task before the checklist",
            "properties": {"type": {"type": "string", "enum": ["end_by", "last"]}, "time": _HHMM},
            "required": ["type"],
            "additionalProperties": False,
        },
        "heavy": {"type": "boolean", "description": "physically heavy; planned earlier in the day"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "e.g. dinner"},
        "thursday_ok": {"type": "boolean", "description": "may be moved to Thursday night"},
    },
    "required": ["name", "minutes"],
    "additionalProperties": False,
}

_CHANGE = {
    "type": "object",
    "description": ("One change. skip_task{task} | skip_tags{tags} | set_minutes{task,minutes} | "
                    "start_time{time} | move_to_thursday{tasks} | add_task{new_task}"),
    "properties": {
        "type": {"type": "string", "enum": ["skip_task", "skip_tags", "set_minutes", "start_time",
                                            "move_to_thursday", "add_task"]},
        "task": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "tasks": {"type": "array", "items": {"type": "string"}},
        "minutes": {"type": "integer", "minimum": 1, "maximum": 600},
        "time": _HHMM,
        "new_task": _TASK,
    },
    "required": ["type"],
    "additionalProperties": False,
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}}


TOOL_SPECS = [
    _tool("get_shabbat_times",
          "Candle lighting and havdalah for the user's city on that Friday, from Hebcal. "
          "Also returns the ready time (candle lighting minus the buffer). Call before build_timeline.",
          {"friday_date": _DATE}, ["friday_date"]),
    _tool("update_profile",
          "Save permanent settings. Send only the fields that change. The first save needs city and "
          "ready_buffer_min. thursday_night_end 00:00 means midnight.",
          {"city": {"type": "string"},
           "ready_buffer_min": {"type": "integer", "minimum": 0, "maximum": 60,
                                "description": "minutes before candle lighting to be completely ready"},
           "friday_start": {**_HHMM, "description": "when Friday preparation can start"},
           "thursday_night_start": _HHMM, "thursday_night_end": _HHMM},
          []),
    _tool("upsert_tasks",
          "Add or replace permanent task templates by name. Send all new tasks in one call. "
          "The whole call is rejected if any task is invalid.",
          {"tasks": {"type": "array", "items": _TASK, "minItems": 1}}, ["tasks"]),
    _tool("list_tasks",
          "All saved task templates, one line each (minutes, kind, priority, dependencies, anchors), "
          "plus the checklist. Minutes marked * are learned from real weeks.",
          {}, []),
    _tool("remove_task",
          "PERMANENTLY delete a task template (and remove it from other tasks' depends_on). "
          "For one week only, use set_week_override with skip_task instead.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("set_checklist",
          "Save the last-minute checklist done right before the ready time, in order. "
          "mode replace (default) or append.",
          {"items": {"type": "array", "minItems": 1, "items": {
              "type": "object",
              "properties": {"text": {"type": "string"},
                             "minutes": {"type": "integer", "minimum": 1, "maximum": 30}},
              "required": ["text", "minutes"], "additionalProperties": False}},
           "mode": {"type": "string", "enum": ["replace", "append"]}},
          ["items"]),
    _tool("remove_checklist_item",
          "PERMANENTLY delete one checklist item by its text.",
          {"text": {"type": "string"}}, ["text"]),
    _tool("log_actuals",
          "Weekly review: save how long tasks really took on that Friday. Updates learned durations "
          "(mean of the last 3 weeks) used in future plans. Templates are not changed.",
          {"friday_date": _DATE,
           "entries": {"type": "array", "minItems": 1, "items": {
               "type": "object",
               "properties": {"task": {"type": "string"},
                              "minutes": {"type": "integer", "minimum": 1, "maximum": 600}},
               "required": ["task", "minutes"], "additionalProperties": False}}},
          ["friday_date", "entries"]),
    _tool("set_week_override",
          "One-time change for one Friday only (guests, a late start, an approved Thursday move). "
          "Never changes templates; expires after that Shabbat.",
          {"friday_date": _DATE, "change": _CHANGE,
           "note": {"type": "string", "description": "why, in a few words"}},
          ["friday_date", "change"]),
    _tool("build_timeline",
          "Build the Friday plan with the deterministic scheduler, one line per block. Uses learned "
          "durations and this week's overrides. For a live re-plan pass now and delay_minutes.",
          {"friday_date": _DATE,
           "now": {"type": "string", "description": "YYYY-MM-DDTHH:MM, only for a live re-plan"},
           "delay_minutes": {"type": "integer", "minimum": 0, "maximum": 600,
                             "description": "how far behind the plan the user is"}},
          ["friday_date"]),
]

TOOL_NAMES = [t["name"] for t in TOOL_SPECS]
