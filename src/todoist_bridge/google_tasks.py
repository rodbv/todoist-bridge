"""Google Tasks REST client for one task list, authenticated with an OAuth refresh token."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx

from todoist_bridge.sync import Task

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://tasks.googleapis.com/tasks/v1"
SCOPE = "https://www.googleapis.com/auth/tasks"

_TOKEN_REFRESH_MARGIN_SECONDS = 60


class GoogleTasksClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        tasklist: str = "@default",
        http: httpx.Client | None = None,
    ) -> None:
        self._credentials = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        self._tasks_url = f"{API_URL}/lists/{tasklist}/tasks"
        self._http = http or httpx.Client(timeout=15)
        self._access_token: str | None = None
        self._expires_at = 0.0

    def list_tasks(self, completed_since: datetime) -> list[Task]:
        pending = self._pages({"showCompleted": "false"})
        recently_completed = (
            t
            for t in self._pages(
                {
                    "showCompleted": "true",
                    "showHidden": "true",
                    "completedMin": completed_since.isoformat(),
                }
            )
            if t.get("status") == "completed"
        )
        return [*pending, *recently_completed]

    def insert_task(self, title: str, notes: str, due: str) -> Task:
        return self._request("POST", "", json={"title": title, "notes": notes, "due": due}).json()

    def update_task(self, task_id: str, **fields: Any) -> Task:
        return self._request("PATCH", f"/{task_id}", json=fields).json()

    def delete_task(self, task_id: str) -> None:
        self._request("DELETE", f"/{task_id}")

    def _pages(self, params: dict[str, str]) -> Iterator[Task]:
        params = {**params, "maxResults": "100"}
        while True:
            body = self._request("GET", "", params=params).json()
            yield from body.get("items", [])
            if not (page_token := body.get("nextPageToken")):
                return
            params["pageToken"] = page_token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(
            method,
            self._tasks_url + path,
            headers={"Authorization": f"Bearer {self._token()}"},
            **kwargs,
        )
        response.raise_for_status()
        return response

    def _token(self) -> str:
        if self._access_token is None or time.monotonic() >= self._expires_at:
            response = self._http.post(TOKEN_URL, data=self._credentials)
            response.raise_for_status()
            body = response.json()
            self._access_token = body["access_token"]
            self._expires_at = time.monotonic() + body["expires_in"] - _TOKEN_REFRESH_MARGIN_SECONDS
        return self._access_token
