"""In-memory stand-ins for the Google Tasks and Todoist clients."""

from __future__ import annotations

import itertools
import re
from datetime import date, datetime
from typing import Any

from todoist_bridge.sync import Task

TODAY = date(2026, 11, 19)

_DATE_WORDS = {"amanhã": "2026-11-20", "tomorrow": "2026-11-20", "hoje": "2026-11-19"}


class FakeGoogleTasks:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.writes: list[tuple[str, str]] = []
        self._ids = (f"g{n}" for n in itertools.count(1))

    def add(
        self, title: str, *, notes: str = "", due: str | None = None, status: str = "needsAction"
    ) -> Task:
        task = {"id": next(self._ids), "title": title, "notes": notes, "status": status}
        if due:
            task["due"] = due
        self.tasks[task["id"]] = task
        return task

    def list_tasks(self, completed_since: datetime) -> list[Task]:
        return [dict(t) for t in self.tasks.values()]

    def insert_task(self, title: str, notes: str, due: str) -> Task:
        self.writes.append(("insert", title))
        return self.add(title, notes=notes, due=due)

    def update_task(self, task_id: str, **fields: Any) -> Task:
        self.writes.append(("update", task_id))
        self.tasks[task_id].update(fields)
        return dict(self.tasks[task_id])

    def delete_task(self, task_id: str) -> None:
        self.writes.append(("delete", task_id))
        del self.tasks[task_id]


class FakeTodoist:
    """Quick Add understands a few date words, enough to tell dated tasks apart."""

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
            if not t["checked"] and t["due"] and t["due"]["date"][:10] <= TODAY.isoformat()
        ]

    def get_task(self, task_id: str) -> Task | None:
        task = self.tasks.get(task_id)
        return dict(task) if task and not task["checked"] else None

    def close_task(self, task_id: str) -> None:
        self.writes.append(("close", task_id))
        self.tasks[task_id]["checked"] = True
