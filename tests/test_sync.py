from datetime import UTC, datetime

import pytest
from fakes import FakeGoogleTasks, FakeTodoist

from todoist_bridge.sync import linked_todoist_id, strip_spoken_prefix, sync, todoist_due_date

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def google() -> FakeGoogleTasks:
    return FakeGoogleTasks()


@pytest.fixture
def todoist() -> FakeTodoist:
    return FakeTodoist()


def run(google, todoist):
    google.writes.clear()
    todoist.writes.clear()
    return sync(google, todoist, NOW)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("me lembra de comprar leite amanhã", "comprar leite amanhã"),
        ("Me lembre de ligar pro João", "ligar pro João"),
        ("lembrar de pagar a conta", "pagar a conta"),
        ("lembra pagar a conta", "pagar a conta"),
        ("Remind me to call mom", "call mom"),
        ("Todoist: buy milk", "buy milk"),
        ("todoist, me lembra de buy milk", "buy milk"),
        ("comprar leite", "comprar leite"),
        ("lembrança da viagem", "lembrança da viagem"),
    ],
)
def test_strip_spoken_prefix_removes_only_leading_voice_commands(spoken, expected):
    assert strip_spoken_prefix(spoken) == expected


@pytest.mark.parametrize(
    ("due", "expected"),
    [
        ({"date": "2026-11-19"}, "2026-11-19"),
        ({"date": "2026-11-19T23:00:00"}, "2026-11-19"),
        ({"date": "2026-11-20T02:00:00Z", "timezone": "America/Sao_Paulo"}, "2026-11-19"),
        ({"date": "2026-11-20T02:00:00Z"}, "2026-11-20"),
        (None, None),
    ],
    ids=["date_only", "floating_time", "utc_instant_in_sao_paulo", "utc_instant_no_zone", "none"],
)
def test_todoist_due_date_is_the_local_day_the_user_sees(due, expected):
    assert todoist_due_date({"due": due}) == expected


def test_voice_capture_creates_todoist_task_and_links_the_google_task(google, todoist):
    gtask = google.add("me lembra de comprar leite hoje")

    stats = run(google, todoist)

    ttask = next(iter(todoist.tasks.values()))
    assert ttask["content"] == "comprar leite"
    assert ttask["due"] == {"date": "2026-11-19"}
    assert google.tasks[gtask["id"]]["title"] == "comprar leite"
    assert linked_todoist_id(google.tasks[gtask["id"]]) == ttask["id"]
    assert stats.created_in_todoist == 1


def test_voice_capture_keeps_existing_google_notes_next_to_the_marker(google, todoist):
    gtask = google.add("comprar leite hoje", notes="integral")

    run(google, todoist)

    notes = google.tasks[gtask["id"]]["notes"]
    assert notes.startswith("integral\n")
    assert linked_todoist_id(google.tasks[gtask["id"]]) is not None


def test_voice_capture_uses_google_due_date_when_todoist_finds_no_date(google, todoist):
    google.add("comprar leite", due="2026-11-21T00:00:00.000Z")

    run(google, todoist)

    assert next(iter(todoist.tasks.values()))["due"] == {"date": "2026-11-21"}


def test_voice_capture_due_later_leaves_google_tasks_on_the_next_run(google, todoist):
    gtask = google.add("me lembra de comprar leite amanhã")
    run(google, todoist)

    stats = run(google, todoist)

    assert gtask["id"] not in google.tasks
    assert stats.deleted_in_gtasks == 1
    assert next(iter(todoist.tasks.values()))["checked"] is False


def test_completing_in_google_closes_the_todoist_task_once(google, todoist):
    gtask = google.add("comprar leite hoje")
    run(google, todoist)
    google.update_task(gtask["id"], status="completed")

    first = run(google, todoist)
    second = run(google, todoist)

    assert first.closed_in_todoist == 1
    assert second.closed_in_todoist == 0
    assert all(t["checked"] for t in todoist.tasks.values())
    assert linked_todoist_id(google.tasks[gtask["id"]]) is None


def test_todoist_task_due_today_is_mirrored_into_google(google, todoist):
    ttask = todoist.add("pagar boleto", due="2026-11-19")

    stats = run(google, todoist)

    (gtask,) = google.tasks.values()
    assert gtask["title"] == "pagar boleto"
    assert gtask["due"] == "2026-11-19T00:00:00.000Z"
    assert linked_todoist_id(gtask) == ttask["id"]
    assert stats.created_in_gtasks == 1


def test_overdue_todoist_task_is_mirrored_and_future_task_is_not(google, todoist):
    todoist.add("atrasada", due="2026-11-10")
    todoist.add("futura", due="2026-12-01")
    todoist.add("sem data")

    run(google, todoist)

    assert [g["title"] for g in google.tasks.values()] == ["atrasada"]


def test_late_evening_todoist_task_gets_its_local_day_in_google(google, todoist):
    ttask = todoist.add("pagar boleto")
    ttask["due"] = {"date": "2026-11-20T02:00:00Z", "timezone": "America/Sao_Paulo"}

    run(google, todoist)

    (gtask,) = google.tasks.values()
    assert gtask["due"] == "2026-11-19T00:00:00.000Z"


def test_second_run_without_changes_writes_nothing(google, todoist):
    todoist.add("pagar boleto", due="2026-11-19T15:00:00")
    google.add("comprar leite hoje")
    run(google, todoist)

    run(google, todoist)

    assert google.writes == []
    assert todoist.writes == []


def test_completing_in_todoist_completes_the_google_copy(google, todoist):
    ttask = todoist.add("pagar boleto", due="2026-11-19")
    run(google, todoist)
    todoist.close_task(ttask["id"])

    stats = run(google, todoist)

    (gtask,) = google.tasks.values()
    assert gtask["status"] == "completed"
    assert linked_todoist_id(gtask) is None
    assert stats.closed_in_gtasks == 1


def test_deleting_in_todoist_completes_the_google_copy(google, todoist):
    ttask = todoist.add("pagar boleto", due="2026-11-19")
    run(google, todoist)
    del todoist.tasks[ttask["id"]]

    run(google, todoist)

    (gtask,) = google.tasks.values()
    assert gtask["status"] == "completed"


def test_rescheduling_in_todoist_deletes_the_google_copy_without_closing(google, todoist):
    ttask = todoist.add("pagar boleto", due="2026-11-19")
    run(google, todoist)
    todoist.tasks[ttask["id"]]["due"] = {"date": "2026-11-25"}

    stats = run(google, todoist)

    assert google.tasks == {}
    assert todoist.tasks[ttask["id"]]["checked"] is False
    assert stats.deleted_in_gtasks == 1


def test_renaming_in_todoist_updates_the_google_title(google, todoist):
    ttask = todoist.add("pagar boleto", due="2026-11-19")
    run(google, todoist)
    todoist.tasks[ttask["id"]]["content"] = "pagar boleto da luz"

    stats = run(google, todoist)

    (gtask,) = google.tasks.values()
    assert gtask["title"] == "pagar boleto da luz"
    assert stats.updated_in_gtasks == 1


def test_google_copy_deleted_by_voice_is_mirrored_again(google, todoist):
    todoist.add("pagar boleto", due="2026-11-19")
    run(google, todoist)
    google.tasks.clear()

    stats = run(google, todoist)

    assert [g["title"] for g in google.tasks.values()] == ["pagar boleto"]
    assert stats.created_in_gtasks == 1


def test_completed_google_task_without_marker_is_ignored(google, todoist):
    google.add("comprar leite", status="completed")

    run(google, todoist)

    assert todoist.tasks == {}


def test_failure_on_one_task_is_recorded_and_the_rest_still_sync(google, todoist, monkeypatch):
    google.add("vai falhar")
    google.add("comprar leite hoje")
    original = todoist.quick_add

    def flaky_quick_add(text):
        if text == "vai falhar":
            raise RuntimeError("boom")
        return original(text)

    monkeypatch.setattr(todoist, "quick_add", flaky_quick_add)

    stats = run(google, todoist)

    assert stats.created_in_todoist == 1
    assert stats.errors == ["google task g1: boom"]
