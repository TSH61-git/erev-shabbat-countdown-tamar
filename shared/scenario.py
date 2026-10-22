from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shared.context_line import this_friday
from shared.store import DataStore


@dataclass(frozen=True)
class Turn:
    session: int
    turn: int
    now: datetime
    friday: str
    text: str


@dataclass
class Scenario:
    name: str
    sessions: list[list[Turn]]
    expect: dict

    @property
    def turns(self) -> list[Turn]:
        return [t for s in self.sessions for t in s]


def load_scenario(path: str | Path) -> Scenario:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sessions = []
    for s in data["sessions"]:
        turns = [Turn(s["session"], t["turn"], datetime.fromisoformat(t["now"]), t["friday"],
                      t["text"]) for t in s["turns"]]
        sessions.append(turns)
    sc = Scenario(data.get("name", ""), sessions, data["expect"])
    numbers = [t.turn for t in sc.turns]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"turns must be numbered 1..n in order, got {numbers}")
    if not 6 <= len(numbers) <= 8:
        raise ValueError(f"the assignment allows 6-8 turns, got {len(numbers)}")
    times = [t.now for t in sc.turns]
    if times != sorted(times):
        raise ValueError("turn times must not go backwards")
    for t in sc.turns:
        # The agent derives "this Friday" from now; a mismatch means the file is wrong.
        if this_friday(t.now).isoformat() != t.friday:
            raise ValueError(f"turn {t.turn}: now {t.now} belongs to Friday "
                             f"{this_friday(t.now)}, not {t.friday}")
    return sc


def reset_state(data_dir: str | Path) -> DataStore:
    store = DataStore(data_dir)
    store.reset()
    return store
