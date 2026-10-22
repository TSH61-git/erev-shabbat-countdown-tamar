from __future__ import annotations

from shared.store import DataStore

HISTORY = "history.jsonl"


def log(msg: str) -> None:
    print(f"[history.py] {msg}", flush=True)


def to_jsonable(content: list) -> list[dict]:
    # exclude_none, not exclude_unset: an empty thinking string and its signature
    # must be replayed exactly, or the API rejects the edited history.
    return [b if isinstance(b, dict) else b.model_dump(mode="json", exclude_none=True)
            for b in content]


class History:
    def __init__(self, store: DataStore):
        self.store = store
        self.seq = len(store.read_jsonl(HISTORY))

    def _append(self, record: dict) -> int:
        self.seq += 1
        self.store.append_jsonl(HISTORY, {"seq": self.seq, **record})
        return self.seq

    def start_session(self, session: int) -> None:
        self._append({"type": "session_start", "session": session})
        log(f"session {session} start, {self.seq - 1} earlier records on disk")

    def append_message(self, message: dict, prompt_tokens: int | None = None) -> None:
        msg = {"role": message["role"], "content": to_jsonable(message["content"])}
        record = {"type": "message", "message": msg}
        # Stored next to the reply so a new agent instance (session 2) still knows
        # how big the conversation is when it decides whether to compact.
        if prompt_tokens is not None:
            record["prompt_tokens"] = prompt_tokens
        seq = self._append(record)
        log(f"append {msg['role']} ({len(msg['content'])} blocks) seq={seq}")

    def last_prompt_tokens(self) -> int:
        _, records = self.active_records()
        return next((r["prompt_tokens"] for r in reversed(records) if "prompt_tokens" in r), 0)

    def append_compaction(self, summary: str, first_seq: int, last_seq: int) -> None:
        seq = self._append({"type": "compaction", "summary": summary,
                            "covers_seq": [first_seq, last_seq]})
        log(f"compaction marker seq={seq} covers {first_seq}-{last_seq}")

    def active_records(self) -> tuple[str | None, list[dict]]:
        records = self.store.read_jsonl(HISTORY)
        cut = max((i for i, r in enumerate(records) if r["type"] == "compaction"), default=-1)
        summary = records[cut]["summary"] if cut >= 0 else None
        return summary, [r for r in records[cut + 1:] if r["type"] == "message"]

    def load_active(self) -> list[dict]:
        summary, records = self.active_records()
        messages = [r["message"] for r in records]
        if summary is not None:
            block = {"type": "text", "text": f"<summary>\n{summary}\n</summary>"}
            # Merge into the next user message: two user messages in a row would be
            # merged by the API anyway, but doing it here keeps the bytes explicit.
            if messages and messages[0]["role"] == "user":
                messages[0] = {"role": "user", "content": [block] + messages[0]["content"]}
            else:
                messages.insert(0, {"role": "user", "content": [block]})
        return messages
