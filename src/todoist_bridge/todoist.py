"""Todoist API v1 client covering the calls the sync needs."""

from __future__ import annotations

from typing import Any

import httpx

from todoist_bridge.sync import Task

API_URL = "https://api.todoist.com/api/v1"
DUE_FILTER = "today | overdue"

_GONE = {404, 410}


class TodoistClient:
    def __init__(self, api_token: str, *, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=15)
        self._headers = {"Authorization": f"Bearer {api_token}"}

    def quick_add(self, text: str) -> Task:
        return self._request("POST", "/tasks/quick", json={"text": text, "auto_reminder": True})

    def set_due_date(self, task_id: str, date: str) -> Task:
        return self._request("POST", f"/tasks/{task_id}", json={"due_date": date})

    def due_tasks(self) -> list[Task]:
        tasks: list[Task] = []
        params = {"query": DUE_FILTER, "limit": "200"}
        while True:
            body = self._request("GET", "/tasks/filter", params=params)
            if isinstance(body, list):
                return tasks + body
            tasks += body.get("results", [])
            if not (cursor := body.get("next_cursor")):
                return tasks
            params["cursor"] = cursor

    def get_task(self, task_id: str) -> Task | None:
        """Return the task while it is active, or None once it is completed or deleted."""
        return self._request("GET", f"/tasks/{task_id}", missing_ok=True)

    def close_task(self, task_id: str) -> None:
        self._request("POST", f"/tasks/{task_id}/close", missing_ok=True)

    def _request(self, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any) -> Any:
        response = self._http.request(method, API_URL + path, headers=self._headers, **kwargs)
        if missing_ok and response.status_code in _GONE:
            return None
        response.raise_for_status()
        return response.json() if response.content else None
