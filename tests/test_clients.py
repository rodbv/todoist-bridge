import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from todoist_bridge.google_tasks import TOKEN_URL, GoogleTasksClient
from todoist_bridge.todoist import TodoistClient


def recording_client(handler):
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(transport)), requests


def google_client(handler):
    def with_token(request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return handler(request)

    http, requests = recording_client(with_token)
    return GoogleTasksClient("id", "secret", "refresh", http=http), requests


def test_google_list_tasks_returns_pending_and_recently_completed_across_pages():
    def handler(request):
        params = request.url.params
        if params["showCompleted"] == "false":
            if params.get("pageToken") == "p2":
                return httpx.Response(200, json={"items": [{"id": "b", "status": "needsAction"}]})
            return httpx.Response(
                200,
                json={"items": [{"id": "a", "status": "needsAction"}], "nextPageToken": "p2"},
            )
        assert params["completedMin"] == "2026-11-16T12:00:00+00:00"
        return httpx.Response(200, json={"items": [{"id": "c", "status": "completed"}]})

    client, requests = google_client(handler)

    tasks = client.list_tasks(completed_since=datetime(2026, 11, 16, 12, tzinfo=UTC))

    assert [t["id"] for t in tasks] == ["a", "b", "c"]
    assert sum(str(r.url) == TOKEN_URL for r in requests) == 1


def test_google_update_task_patches_only_the_given_fields_with_bearer_token():
    client, requests = google_client(lambda request: httpx.Response(200, json={"id": "g1"}))

    client.update_task("g1", status="completed", notes="")

    patch = requests[-1]
    assert patch.method == "PATCH"
    assert patch.url.path == "/tasks/v1/lists/@default/tasks/g1"
    assert patch.headers["Authorization"] == "Bearer tok"
    assert json.loads(patch.content) == {"status": "completed", "notes": ""}


def test_google_token_refresh_sends_refresh_token_grant():
    client, requests = google_client(lambda request: httpx.Response(204))

    client.delete_task("g1")

    form = dict(httpx.QueryParams(requests[0].content.decode()))
    assert form == {
        "client_id": "id",
        "client_secret": "secret",
        "refresh_token": "refresh",
        "grant_type": "refresh_token",
    }


def test_todoist_quick_add_asks_for_the_default_reminder():
    http, requests = recording_client(lambda request: httpx.Response(200, json={"id": "t1"}))

    TodoistClient("token", http=http).quick_add("comprar leite amanhã às 16h")

    assert requests[0].url.path == "/api/v1/tasks/quick"
    assert json.loads(requests[0].content) == {
        "text": "comprar leite amanhã às 16h",
        "auto_reminder": True,
    }


SAO_PAULO = ZoneInfo("America/Sao_Paulo")
OVERDUE = {"date": "2000-01-01"}


NOW_IN_SAO_PAULO = datetime(2026, 11, 19, 21, 30, tzinfo=SAO_PAULO)


def todoist_with_user_in_sao_paulo(filter_handler):
    def handler(request):
        if request.url.path == "/api/v1/user":
            return httpx.Response(200, json={"tz_info": {"timezone": "America/Sao_Paulo"}})
        return filter_handler(request)

    http, requests = recording_client(handler)
    client = TodoistClient("token", http=http, clock=lambda tz: NOW_IN_SAO_PAULO.astimezone(tz))
    return client, requests


def test_todoist_due_tasks_follows_the_cursor():
    def handler(request):
        assert request.url.params["query"] == "today | overdue | tomorrow"
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(
                200, json={"results": [{"id": "t2", "due": OVERDUE}], "next_cursor": None}
            )
        return httpx.Response(
            200, json={"results": [{"id": "t1", "due": OVERDUE}], "next_cursor": "c2"}
        )

    client, _ = todoist_with_user_in_sao_paulo(handler)

    assert [t["id"] for t in client.due_tasks()] == ["t1", "t2"]


def test_todoist_due_tasks_accepts_a_plain_list_response():
    client, _ = todoist_with_user_in_sao_paulo(
        lambda request: httpx.Response(200, json=[{"id": "t1", "due": OVERDUE}])
    )

    assert [t["id"] for t in client.due_tasks()] == ["t1"]


def test_todoist_due_tasks_keeps_tonight_and_drops_tomorrow_by_local_day():
    tasks = [
        {
            "id": "tonight",
            "due": {"date": "2026-11-20T02:00:00Z", "timezone": "America/Sao_Paulo"},
        },
        {"id": "today", "due": {"date": "2026-11-19"}},
        {"id": "tomorrow", "due": {"date": "2026-11-20"}},
        {"id": "tomorrow-at-10", "due": {"date": "2026-11-20T10:00:00"}},
        {"id": "undated", "due": None},
    ]
    client, _ = todoist_with_user_in_sao_paulo(
        lambda request: httpx.Response(200, json={"results": tasks, "next_cursor": None})
    )

    assert [t["id"] for t in client.due_tasks()] == ["tonight", "today"]


def test_todoist_get_task_returns_none_for_deleted_task():
    http, _ = recording_client(lambda request: httpx.Response(404))

    assert TodoistClient("token", http=http).get_task("t1") is None


def test_todoist_close_task_tolerates_a_task_that_is_already_gone():
    http, requests = recording_client(lambda request: httpx.Response(404))

    TodoistClient("token", http=http).close_task("t1")

    assert requests[0].url.path == "/api/v1/tasks/t1/close"


def test_todoist_recurring_tasks_uses_the_recurring_filter():
    http, requests = recording_client(
        lambda request: httpx.Response(200, json={"results": [{"id": "t1"}], "next_cursor": None})
    )

    assert TodoistClient("token", http=http).recurring_tasks() == [{"id": "t1"}]
    assert requests[0].url.params["query"] == "recurring"


def test_todoist_delete_task_tolerates_a_task_that_is_already_gone():
    http, requests = recording_client(lambda request: httpx.Response(404))

    TodoistClient("token", http=http).delete_task("t1")

    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/api/v1/tasks/t1"
