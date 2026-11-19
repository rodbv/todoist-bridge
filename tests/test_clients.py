import json
from datetime import UTC, datetime

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


def test_todoist_due_tasks_follows_the_cursor():
    def handler(request):
        assert request.url.params["query"] == "today | overdue"
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(200, json={"results": [{"id": "t2"}], "next_cursor": None})
        return httpx.Response(200, json={"results": [{"id": "t1"}], "next_cursor": "c2"})

    http, _ = recording_client(handler)

    assert [t["id"] for t in TodoistClient("token", http=http).due_tasks()] == ["t1", "t2"]


def test_todoist_due_tasks_accepts_a_plain_list_response():
    http, _ = recording_client(lambda request: httpx.Response(200, json=[{"id": "t1"}]))

    assert TodoistClient("token", http=http).due_tasks() == [{"id": "t1"}]


def test_todoist_get_task_returns_none_for_completed_or_deleted_task():
    http, _ = recording_client(lambda request: httpx.Response(404))

    assert TodoistClient("token", http=http).get_task("t1") is None


def test_todoist_close_task_tolerates_a_task_that_is_already_gone():
    http, requests = recording_client(lambda request: httpx.Response(404))

    TodoistClient("token", http=http).close_task("t1")

    assert requests[0].url.path == "/api/v1/tasks/t1/close"
