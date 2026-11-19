"""Command line entry point: ``todoist-bridge sync`` and ``todoist-bridge auth``."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from todoist_bridge.google_tasks import GoogleTasksClient
from todoist_bridge.oauth import obtain_refresh_token
from todoist_bridge.sync import GoogleTasks, Stats, Task, Todoist, sync
from todoist_bridge.todoist import TodoistClient

REQUIRED_ENV = (
    "TODOIST_API_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
)


def run_sync(*, dry_run: bool = False) -> Stats:
    if missing := [name for name in REQUIRED_ENV if not os.environ.get(name)]:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")
    google: GoogleTasks = GoogleTasksClient(
        os.environ["GOOGLE_CLIENT_ID"],
        os.environ["GOOGLE_CLIENT_SECRET"],
        os.environ["GOOGLE_REFRESH_TOKEN"],
        tasklist=os.environ.get("GOOGLE_TASKLIST", "@default"),
    )
    todoist: Todoist = TodoistClient(os.environ["TODOIST_API_TOKEN"])
    if dry_run:
        google, todoist = DryRunGoogleTasks(google), DryRunTodoist(todoist)
    return sync(google, todoist, datetime.now(UTC))


class DryRunGoogleTasks:
    """Reads from Google Tasks and prints the writes instead of sending them."""

    def __init__(self, real: GoogleTasks) -> None:
        self._real = real

    def list_tasks(self, completed_since: datetime) -> list[Task]:
        return self._real.list_tasks(completed_since)

    def insert_task(self, title: str, notes: str, due: str) -> Task:
        _would("insert Google task", title=title, notes=notes, due=due)
        return {"id": "dry-run"}

    def update_task(self, task_id: str, **fields: Any) -> Task:
        _would(f"update Google task {task_id}", **fields)
        return {"id": task_id, **fields}

    def delete_task(self, task_id: str) -> None:
        _would(f"delete Google task {task_id}")


class DryRunTodoist:
    """Reads from Todoist and prints the writes instead of sending them."""

    def __init__(self, real: Todoist) -> None:
        self._real = real

    def quick_add(self, text: str) -> Task:
        _would("quick add to Todoist", text=text)
        return {"id": "dry-run", "content": text, "due": None}

    def set_due_date(self, task_id: str, date: str) -> Task:
        _would(f"set Todoist due date on {task_id}", date=date)
        return {"id": task_id, "content": "(dry run)", "due": {"date": date}}

    def due_tasks(self) -> list[Task]:
        return self._real.due_tasks()

    def get_task(self, task_id: str) -> Task | None:
        return self._real.get_task(task_id)

    def close_task(self, task_id: str) -> None:
        _would(f"close Todoist task {task_id}")


def _would(action: str, **fields: Any) -> None:
    details = ", ".join(f"{key}={value!r}" for key, value in fields.items())
    print(f"[dry run] would {action}" + (f": {details}" if details else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="todoist-bridge", description="Sync Google Tasks and Todoist."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sync_parser = commands.add_parser("sync", help="run one sync between Google Tasks and Todoist")
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="read both sides and print the writes"
    )
    commands.add_parser("auth", help="obtain a Google refresh token in the browser")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "auth":
        client_id = os.environ.get("GOOGLE_CLIENT_ID") or input("Google OAuth client id: ")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET") or input("Google client secret: ")
        token = obtain_refresh_token(client_id.strip(), client_secret.strip())
        print(f"Add this line to your .env:\nGOOGLE_REFRESH_TOKEN={token}")
        return 0

    stats = run_sync(dry_run=args.dry_run)
    print(json.dumps(stats.as_dict(), ensure_ascii=False))
    return 1 if stats.errors else 0
