"""In-memory stand-ins for the Google Tasks and Todoist clients."""

from __future__ import annotations

import itertools
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from todoist_bridge.sync import Task, todoist_due_date

TODAY = date(2026, 11, 19)

_DUE_DATES = {
    "amanhã": "2026-11-20",
    "tomorrow": "2026-11-20",
    "hoje": "2026-11-19",
    "às 16h": "2026-11-19T16:00:00",
    "todo dia": "2026-11-20",
}
_seconds = itertools.count(1)


def _timestamp() -> str:
    """One clock for both fakes, so edit times compare across apps like the real ones."""
    instant = datetime(2026, 11, 19, 12, 0, tzinfo=UTC) + timedelta(seconds=next(_seconds))
    return instant.isoformat().replace("+00:00", "Z")


def _parse_due(string: str) -> dict[str, Any]:
    due = {"date": _DUE_DATES[string], "string": string, "lang": "pt"}
    if string == "todo dia":
        due["is_recurring"] = True
    return due


class FakeGoogleTasks:
    """Like Google, removing or completing a recurring task brings back its next
    instance with the original title and no notes."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.writes: list[tuple[str, str]] = []
        self._ids = (f"g{n}" for n in itertools.count(1))
        self._series: dict[str, str] = {}

    def add(
        self,
        title: str,
        *,
        notes: str = "",
        due: str | None = None,
        status: str = "needsAction",
        recurring: bool = False,
    ) -> Task:
        task = {
            "id": next(self._ids),
            "title": title,
            "notes": notes,
            "status": status,
            "updated": _timestamp(),
        }
        if due:
            task["due"] = due
        self.tasks[task["id"]] = task
        if recurring:
            self._series[task["id"]] = title
        return task

    def _next_instance(self, task_id: str) -> None:
        if title := self._series.pop(task_id, None):
            self.add(title, recurring=True)

    def list_tasks(self, completed_since: datetime) -> list[Task]:
        return [dict(t) for t in self.tasks.values()]

    def insert_task(self, title: str, notes: str, due: str) -> Task:
        self.writes.append(("insert", title))
        return self.add(title, notes=notes, due=due)

    def update_task(self, task_id: str, **fields: Any) -> Task:
        self.writes.append(("update", task_id))
        self.tasks[task_id].update(fields, updated=_timestamp())
        if fields.get("status") == "completed":
            self._next_instance(task_id)
        return dict(self.tasks[task_id])

    def delete_task(self, task_id: str) -> None:
        self.writes.append(("delete", task_id))
        del self.tasks[task_id]
        self._next_instance(task_id)

    def pending(self) -> list[Task]:
        return [t for t in self.tasks.values() if t["status"] == "needsAction"]


class FakeTodoist:
    """Quick Add and due strings understand a few date words, a time and "todo dia"
    (daily, next due tomorrow), enough to tell dated, timed and recurring tasks apart."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.writes: list[tuple[str, str]] = []
        self._ids = (f"t{n}" for n in itertools.count(1))

    def add(self, content: str, *, due: str | None = None) -> Task:
        task = {
            "id": next(self._ids),
            "content": content,
            "checked": False,
            "due": {"date": due} if due else None,
            "updated_at": _timestamp(),
        }
        self.tasks[task["id"]] = task
        return task

    def quick_add(self, text: str) -> Task:
        self.writes.append(("quick_add", text))
        task = self.add(text)
        for string in _DUE_DATES:
            if re.search(rf"\b{string}\b", text):
                task["content"] = re.sub(rf"\s*\b{string}\b", "", text).strip()
                task["due"] = _parse_due(string)
                break
        return dict(task)

    def set_due_date(self, task_id: str, date: str) -> Task:
        self.writes.append(("set_due_date", task_id))
        self.tasks[task_id].update(due={"date": date}, updated_at=_timestamp())
        return dict(self.tasks[task_id])

    def update_task(self, task_id: str, **fields: Any) -> Task:
        self.writes.append(("update", task_id))
        task = self.tasks[task_id]
        if "content" in fields:
            task["content"] = fields["content"]
        if "due_string" in fields:
            task["due"] = _parse_due(fields["due_string"])
        task["updated_at"] = _timestamp()
        return dict(task)

    def due_tasks(self) -> list[Task]:
        return [
            dict(t)
            for t in self.tasks.values()
            if not t["checked"] and (todoist_due_date(t) or "9999") <= TODAY.isoformat()
        ]

    def get_task(self, task_id: str) -> Task | None:
        """Like the API: completed tasks come back with checked set, deleted ones as None."""
        task = self.tasks.get(task_id)
        return dict(task) if task else None

    def close_task(self, task_id: str) -> None:
        self.writes.append(("close", task_id))
        task = self.tasks[task_id]
        if task["due"] and task["due"].get("is_recurring"):
            next_day = date.fromisoformat(task["due"]["date"][:10]) + timedelta(days=1)
            task["due"]["date"] = next_day.isoformat()
        else:
            task["checked"] = True
        task["updated_at"] = _timestamp()

    def delete_task(self, task_id: str) -> None:
        self.writes.append(("delete", task_id))
        self.tasks.pop(task_id, None)

    def recurring_tasks(self) -> list[Task]:
        return [
            dict(t)
            for t in self.tasks.values()
            if not t["checked"] and t["due"] and t["due"].get("is_recurring")
        ]
