"""In-memory stand-ins for the Google Tasks and Todoist clients."""

from __future__ import annotations

import itertools
import re
from datetime import date, datetime, timedelta
from typing import Any

from todoist_bridge.sync import Task, todoist_due_date

TODAY = date(2026, 11, 19)

_DATE_WORDS = {"amanhã": "2026-11-20", "tomorrow": "2026-11-20", "hoje": "2026-11-19"}


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
        task = {"id": next(self._ids), "title": title, "notes": notes, "status": status}
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
        self.tasks[task_id].update(fields)
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
    """Quick Add understands a few date words and "todo dia" (daily, next due tomorrow),
    enough to tell dated and recurring tasks apart."""

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
        }
        self.tasks[task["id"]] = task
        return task

    def quick_add(self, text: str) -> Task:
        self.writes.append(("quick_add", text))
        if "todo dia" in text:
            task = self.add(text.replace("todo dia", "").strip(), due="2026-11-20")
            task["due"].update(string="todo dia", is_recurring=True)
            return dict(task)
        due = None
        for word, day in _DATE_WORDS.items():
            if re.search(rf"\b{word}\b", text):
                due = day
                text = re.sub(rf"\s*\b{word}\b", "", text)
        return dict(self.add(text.strip(), due=due))

    def set_due_date(self, task_id: str, date: str) -> Task:
        self.writes.append(("set_due_date", task_id))
        self.tasks[task_id]["due"] = {"date": date}
        return dict(self.tasks[task_id])

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

    def delete_task(self, task_id: str) -> None:
        self.writes.append(("delete", task_id))
        self.tasks.pop(task_id, None)

    def recurring_tasks(self) -> list[Task]:
        return [
            dict(t)
            for t in self.tasks.values()
            if not t["checked"] and t["due"] and t["due"].get("is_recurring")
        ]
