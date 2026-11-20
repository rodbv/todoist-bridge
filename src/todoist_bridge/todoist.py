"""Todoist API v1 client covering the calls the sync needs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from todoist_bridge.sync import Task, todoist_due_date

API_URL = "https://api.todoist.com/api/v1"
# "today" alone misses fixed-timezone tasks whose UTC day is tomorrow (23:00 BRT is 02:00Z).
CANDIDATES_FILTER = "today | overdue | tomorrow"

_GONE = {404, 410}


class TodoistClient:
    def __init__(
        self,
        api_token: str,
        *,
        http: httpx.Client | None = None,
        clock: Callable[[ZoneInfo], datetime] = datetime.now,
    ) -> None:
        self._http = http or httpx.Client(timeout=15)
        self._clock = clock
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._timezone: ZoneInfo | None = None

    def quick_add(self, text: str) -> Task:
        return self._request("POST", "/tasks/quick", json={"text": text, "auto_reminder": True})

    def set_due_date(self, task_id: str, date: str) -> Task:
        return self._request("POST", f"/tasks/{task_id}", json={"due_date": date})

    def due_tasks(self) -> list[Task]:
        """Return active tasks due today or overdue, in the user's Todoist timezone."""
        today = self._clock(self._user_timezone()).date().isoformat()
        return [
            task
            for task in self._candidate_tasks()
            if (due_date := todoist_due_date(task)) is not None and due_date <= today
        ]

    def get_task(self, task_id: str) -> Task | None:
        """Return the task, or None once it is deleted. Completed tasks have ``checked`` set."""
        return self._request("GET", f"/tasks/{task_id}", missing_ok=True)

    def close_task(self, task_id: str) -> None:
        self._request("POST", f"/tasks/{task_id}/close", missing_ok=True)

    def delete_task(self, task_id: str) -> None:
        self._request("DELETE", f"/tasks/{task_id}", missing_ok=True)

    def recurring_tasks(self) -> list[Task]:
        return self._filter("recurring")

    def _candidate_tasks(self) -> list[Task]:
        return self._filter(CANDIDATES_FILTER)

    def _filter(self, query: str) -> list[Task]:
        tasks: list[Task] = []
        params = {"query": query, "limit": "200"}
        while True:
            body = self._request("GET", "/tasks/filter", params=params)
            if isinstance(body, list):
                return tasks + body
            tasks += body.get("results", [])
            if not (cursor := body.get("next_cursor")):
                return tasks
            params["cursor"] = cursor

    def user(self) -> dict[str, Any]:
        return self._request("GET", "/user")

    def _user_timezone(self) -> ZoneInfo:
        if self._timezone is None:
            self._timezone = ZoneInfo(self.user()["tz_info"]["timezone"])
        return self._timezone

    def _request(self, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any) -> Any:
        response = self._http.request(method, API_URL + path, headers=self._headers, **kwargs)
        if missing_ok and response.status_code in _GONE:
            return None
        response.raise_for_status()
        return response.json() if response.content else None
