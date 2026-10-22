TASKS = [
    {"name": "shopping", "minutes": 50, "kind": "active", "depends_on": [], "priority": "must",
     "anchor": None, "heavy": False, "tags": [], "thursday_ok": False},
    {"name": "challah", "minutes": 60, "kind": "active", "depends_on": [], "priority": "must",
     "anchor": None, "heavy": False, "tags": [], "thursday_ok": False},
    {"name": "challah_rise", "minutes": 45, "kind": "passive", "depends_on": ["challah"],
     "priority": "must", "anchor": None, "heavy": False, "tags": [], "thursday_ok": False},
    {"name": "challah_bake", "minutes": 30, "kind": "passive", "depends_on": ["challah_rise"],
     "priority": "must", "anchor": None, "heavy": False, "tags": [], "thursday_ok": False},
    {"name": "cooking", "minutes": 15, "kind": "active", "depends_on": ["shopping"],
     "priority": "must", "anchor": None, "heavy": False, "tags": ["dinner"], "thursday_ok": True},
    {"name": "slow_cook", "minutes": 150, "kind": "passive", "depends_on": ["cooking"],
     "priority": "must", "anchor": None, "heavy": False, "tags": ["dinner"], "thursday_ok": False},
    {"name": "cleaning", "minutes": 50, "kind": "active", "depends_on": [], "priority": "must",
     "anchor": None, "heavy": True, "tags": [], "thursday_ok": True},
    {"name": "set_table", "minutes": 15, "kind": "active", "depends_on": [], "priority": "must",
     "anchor": {"type": "end_by", "time": "15:00"}, "heavy": False, "tags": [], "thursday_ok": False},
    {"name": "flowers", "minutes": 15, "kind": "active", "depends_on": [], "priority": "nice",
     "anchor": None, "heavy": False, "tags": [], "thursday_ok": False},
    {"name": "shower", "minutes": 20, "kind": "active", "depends_on": [], "priority": "must",
     "anchor": {"type": "last"}, "heavy": False, "tags": [], "thursday_ok": False},
]

CHECKLIST = [
    {"text": "Turn off the phone", "minutes": 2},
    {"text": "Set the alarm", "minutes": 2},
    {"text": "Turn on the hot plate", "minutes": 3},
    {"text": "Set the light timers", "minutes": 3},
]

WEEK1 = "2026-10-30"
WEEK2 = "2026-11-06"
