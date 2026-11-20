"""Two-way sync between a Google Tasks list and Todoist "today | overdue".

Google Tasks holds the Todoist tasks due today or overdue, plus voice captures
that have not reached Todoist yet. Each mirrored Google task carries the Todoist
id in its notes (``todoist:<id>``), so the mapping lives with the data and the
sync keeps no state of its own.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

Task = dict[str, Any]

COMPLETED_LOOKBACK = timedelta(days=3)

_MARKER_RE = re.compile(r"\btodoist:([A-Za-z0-9_-]+)(:voice)?")
_SPOKEN_PREFIX_RE = re.compile(
    r"^\s*(?:todoist|remind me(?: to)?|me lembr[ae](?: de)?|lembrar?(?: de)?)\s*[:,]?\s+",
    re.IGNORECASE,
)


class GoogleTasks(Protocol):
    def list_tasks(self, completed_since: datetime) -> list[Task]: ...
    def insert_task(self, title: str, notes: str, due: str) -> Task: ...
    def update_task(self, task_id: str, **fields: Any) -> Task: ...
    def delete_task(self, task_id: str) -> None: ...


class Todoist(Protocol):
    def quick_add(self, text: str) -> Task: ...
    def set_due_date(self, task_id: str, date: str) -> Task: ...
    def due_tasks(self) -> list[Task]: ...
    def get_task(self, task_id: str) -> Task | None: ...
    def close_task(self, task_id: str) -> None: ...
    def delete_task(self, task_id: str) -> None: ...
    def recurring_tasks(self) -> list[Task]: ...


@dataclass
class Stats:
    created_in_todoist: int = 0
    closed_in_todoist: int = 0
    created_in_gtasks: int = 0
    updated_in_gtasks: int = 0
    closed_in_gtasks: int = 0
    deleted_in_gtasks: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_spoken_prefix(title: str) -> str:
    while (stripped := _SPOKEN_PREFIX_RE.sub("", title, count=1)) != title:
        title = stripped
    return title.strip()


def voice_marker_for(todoist_id: str) -> str:
    return f"todoist:{todoist_id}:voice"


def is_voice_capture(gtask: Task) -> bool:
    match = _MARKER_RE.search(gtask.get("notes") or "")
    return bool(match and match.group(2))


def is_recurring(ttask: Task) -> bool:
    return bool((ttask.get("due") or {}).get("is_recurring"))


def marker_for(todoist_id: str) -> str:
    return f"todoist:{todoist_id}"


def linked_todoist_id(gtask: Task) -> str | None:
    match = _MARKER_RE.search(gtask.get("notes") or "")
    return match.group(1) if match else None


def notes_without_marker(gtask: Task) -> str:
    return _MARKER_RE.sub("", gtask.get("notes") or "").strip()


def google_due(date: str) -> str:
    return f"{date[:10]}T00:00:00.000Z"


def todoist_due_date(ttask: Task) -> str | None:
    """Return the local due day; fixed-timezone tasks arrive as UTC instants ("...Z")."""
    due = ttask.get("due")
    if not due:
        return None
    if not due["date"].endswith("Z"):
        return due["date"][:10]
    instant = datetime.fromisoformat(due["date"])
    return instant.astimezone(ZoneInfo(due.get("timezone") or "UTC")).date().isoformat()


def sync(google: GoogleTasks, todoist: Todoist, now: datetime) -> Stats:
    stats = Stats()
    gtasks = google.list_tasks(completed_since=now - COMPLETED_LOOKBACK)
    due_in_todoist = {t["id"]: t for t in todoist.due_tasks()}
    linked_ids = {tid for g in gtasks if (tid := linked_todoist_id(g))}
    closed_ids: set[str] = set()

    for gtask in gtasks:
        tid = linked_todoist_id(gtask)
        pending = gtask.get("status") == "needsAction"
        try:
            if pending and tid is None:
                _send_voice_capture_to_todoist(google, todoist, gtask, stats)
            elif not pending and tid is not None:
                _close_in_todoist(google, todoist, gtask, tid, closed_ids, stats)
            elif pending and tid in due_in_todoist:
                _refresh_mirror(google, gtask, due_in_todoist[tid], stats)
            elif pending:
                _retire_mirror(google, todoist, gtask, tid, stats)
        except Exception as exc:
            _record_error(stats, f"google task {gtask.get('id')}", exc)

    for tid, ttask in due_in_todoist.items():
        if tid in linked_ids:
            continue
        try:
            google.insert_task(
                title=ttask["content"],
                notes=marker_for(tid),
                due=google_due(todoist_due_date(ttask) or now.date().isoformat()),
            )
            stats.created_in_gtasks += 1
        except Exception as exc:
            _record_error(stats, f"todoist task {tid}", exc)

    return stats


def _send_voice_capture_to_todoist(
    google: GoogleTasks, todoist: Todoist, gtask: Task, stats: Stats
) -> None:
    ttask = todoist.quick_add(strip_spoken_prefix(gtask.get("title") or ""))
    if twin := _recurring_twin(todoist, ttask):
        # A recurring Google task brings back its next instance without the marker;
        # link that instance to the series already in Todoist.
        todoist.delete_task(ttask["id"])
        ttask = twin
    else:
        stats.created_in_todoist += 1
    if ttask.get("due") is None and gtask.get("due"):
        ttask = todoist.set_due_date(ttask["id"], gtask["due"][:10])
    changes: dict[str, Any] = {
        "title": ttask["content"],
        "notes": "\n".join(filter(None, [gtask.get("notes"), voice_marker_for(ttask["id"])])),
    }
    if date := todoist_due_date(ttask):
        changes["due"] = google_due(date)
    google.update_task(gtask["id"], **changes)


def _close_in_todoist(
    google: GoogleTasks,
    todoist: Todoist,
    gtask: Task,
    tid: str,
    closed_ids: set[str],
    stats: Stats,
) -> None:
    # Deleting a Google series completes all its instances at once; a recurring
    # Todoist task must move forward one occurrence, not one per instance.
    if tid not in closed_ids:
        closed_ids.add(tid)
        if _still_open_at_google_occurrence(todoist.get_task(tid), gtask):
            todoist.close_task(tid)
            stats.closed_in_todoist += 1
    # Without the marker, later runs ignore this task, so a recurring Todoist
    # task never gets closed twice.
    google.update_task(gtask["id"], notes=notes_without_marker(gtask))


def _still_open_at_google_occurrence(ttask: Task | None, gtask: Task) -> bool:
    """A recurring task completed in Todoist first already sits on a later date, and a
    second close would skip one occurrence."""
    if ttask is None or ttask.get("checked"):
        return False
    google_date = (gtask.get("due") or "")[:10]
    todoist_date = todoist_due_date(ttask)
    if is_recurring(ttask) and google_date and todoist_date:
        return todoist_date <= google_date
    return True


def _refresh_mirror(google: GoogleTasks, gtask: Task, ttask: Task, stats: Stats) -> None:
    changes: dict[str, Any] = {}
    if gtask.get("title") != ttask["content"]:
        changes["title"] = ttask["content"]
    date = todoist_due_date(ttask)
    if date and (gtask.get("due") or "")[:10] != date:
        changes["due"] = google_due(date)
    if changes:
        google.update_task(gtask["id"], **changes)
        stats.updated_in_gtasks += 1


def _retire_mirror(
    google: GoogleTasks, todoist: Todoist, gtask: Task, tid: str, stats: Stats
) -> None:
    ttask = todoist.get_task(tid)
    if ttask is None or ttask.get("checked"):
        google.update_task(gtask["id"], status="completed", notes=notes_without_marker(gtask))
        stats.closed_in_gtasks += 1
    elif not is_voice_capture(gtask):
        google.delete_task(gtask["id"])
        stats.deleted_in_gtasks += 1
    else:
        _refresh_mirror(google, gtask, ttask, stats)
    # A voice capture stays until its Todoist task is done: Google cannot say whether
    # it repeats, and removing a recurring one makes Google create the next instance.


def _recurring_twin(todoist: Todoist, ttask: Task) -> Task | None:
    if not is_recurring(ttask):
        return None
    return next(
        (
            other
            for other in todoist.recurring_tasks()
            if other["id"] != ttask["id"]
            and other["content"] == ttask["content"]
            and other["due"].get("string") == ttask["due"].get("string")
        ),
        None,
    )


def _record_error(stats: Stats, subject: str, exc: Exception) -> None:
    log.exception("sync failed for %s", subject)
    stats.errors.append(f"{subject}: {exc}")
