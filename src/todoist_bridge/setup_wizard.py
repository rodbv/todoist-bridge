"""``todoist-bridge setup``: collect and check the credentials, preview the first sync,
deploy to Modal and print the Gemini instructions."""

from __future__ import annotations

import getpass
import json
import subprocess
import sys
import tempfile
import webbrowser
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from todoist_bridge.gemini import SAVED_INFO_URL, saved_info_for
from todoist_bridge.google_tasks import TOKEN_URL, GoogleTasksClient
from todoist_bridge.oauth import obtain_refresh_token
from todoist_bridge.sync import Stats
from todoist_bridge.todoist import TodoistClient

GoogleStatus = Literal["ok", "invalid_client", "invalid_grant", "tasks_api_disabled"]

TODOIST = "TODOIST_API_TOKEN"
CLIENT_ID = "GOOGLE_CLIENT_ID"
CLIENT_SECRET = "GOOGLE_CLIENT_SECRET"
REFRESH_TOKEN = "GOOGLE_REFRESH_TOKEN"
TASKLIST = "GOOGLE_TASKLIST"
SECRET_NAME = "todoist-bridge"

TODOIST_TOKEN_URL = "https://app.todoist.com/app/settings/integrations/developer"
TASKS_API_URL = "https://console.cloud.google.com/apis/library/tasks.googleapis.com"
GOOGLE_STEPS = (
    (
        "Create a project (any name, for example todoist-bridge):",
        "https://console.cloud.google.com/projectcreate",
    ),
    ("Enable the Google Tasks API in that project:", TASKS_API_URL),
    (
        "Fill in Branding. App name: todoist-bridge. Home page: "
        "https://github.com/rodbv/todoist-bridge. Privacy policy: "
        "https://github.com/rodbv/todoist-bridge/blob/main/PRIVACY.md. "
        "Authorized domain: github.com. Leave the logo empty.",
        "https://console.cloud.google.com/auth/branding",
    ),
    (
        "In Audience, choose External, then click Publish app, so your sign-in "
        "does not expire after 7 days:",
        "https://console.cloud.google.com/auth/audience",
    ),
    (
        "Create an OAuth client of type Desktop app and copy its id and secret:",
        "https://console.cloud.google.com/auth/clients/create",
    ),
)
GOOGLE_HELP_URL = "https://github.com/rodbv/todoist-bridge#google-oauth-client"


class Console:
    def ask(self, prompt: str, *, secret: bool = False) -> str:
        return (getpass.getpass if secret else input)(prompt).strip()

    def say(self, text: str = "") -> None:
        print(text)


class Modal(Protocol):
    def logged_in(self) -> bool: ...
    def login(self) -> None: ...
    def put_secret(self, values: Mapping[str, str]) -> None: ...
    def deploy(self) -> None: ...


class ModalCli:
    """Runs the Modal CLI of the current environment."""

    def __init__(self, app_file: Path) -> None:
        self._app_file = app_file

    def logged_in(self) -> bool:
        return self._modal("token", "info", check=False, capture_output=True).returncode == 0

    def login(self) -> None:
        self._modal("setup")

    def put_secret(self, values: Mapping[str, str]) -> None:
        # A JSON file keeps the tokens out of the process list, where any user can read them.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "secret.json"
            path.touch(mode=0o600)
            path.write_text(json.dumps(dict(values)))
            self._modal(
                "secret",
                "create",
                SECRET_NAME,
                "--from-json",
                str(path),
                "--force",
                capture_output=True,
            )

    def deploy(self) -> None:
        self._modal("deploy", str(self._app_file))

    @staticmethod
    def _modal(*args: str, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "modal", *args], check=check, **kwargs)


def todoist_user(token: str) -> dict[str, Any] | None:
    """Return the Todoist user, or None when Todoist refuses the token."""
    try:
        return TodoistClient(token).user()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return None
        raise


def check_google(client_id: str, client_secret: str, refresh_token: str) -> GoogleStatus:
    try:
        GoogleTasksClient(client_id, client_secret, refresh_token).list_tasks(datetime.now(UTC))
    except httpx.HTTPStatusError as exc:
        if str(exc.request.url) == TOKEN_URL:
            error = exc.response.json().get("error")
            if error in ("invalid_client", "unauthorized_client"):
                return "invalid_client"
            if error == "invalid_grant":
                return "invalid_grant"
        elif exc.response.status_code == 403:
            return "tasks_api_disabled"
        raise
    return "ok"


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def write_env(path: Path, values: Mapping[str, str]) -> None:
    """Update the given keys in place and keep every other line, including comments."""
    lines = path.read_text().splitlines() if path.exists() else []
    pending = dict(values)
    for i, line in enumerate(lines):
        key, sep, _ = line.partition("=")
        if sep and key.strip() in pending:
            lines[i] = f"{key.strip()}={pending.pop(key.strip())}"
    lines += [f"{key}={value}" for key, value in pending.items()]
    path.touch(mode=0o600)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + "\n")


class SetupWizard:
    def __init__(
        self,
        env_path: Path,
        *,
        console: Console | None = None,
        todoist_user: Callable[[str], dict[str, Any] | None] = todoist_user,
        check_google: Callable[[str, str, str], GoogleStatus] = check_google,
        obtain_refresh_token: Callable[[str, str], str] = obtain_refresh_token,
        dry_run: Callable[[Mapping[str, str]], Stats],
        modal: Modal,
        open_url: Callable[[str], object] = webbrowser.open,
    ) -> None:
        self._env_path = env_path
        self._console = console or Console()
        self._todoist_user = todoist_user
        self._check_google = check_google
        self._obtain_refresh_token = obtain_refresh_token
        self._dry_run = dry_run
        self._modal = modal
        self._open_url = open_url
        self._env: dict[str, str] = {}

    def run(self) -> int:
        self._env = read_env(self._env_path)
        user = self._connect_todoist()
        self._connect_google()
        if not self._first_sync_is_accepted():
            return 1
        self._deploy()
        self._show_gemini_instructions(user.get("lang") or "en")
        return 0

    def _connect_todoist(self) -> dict[str, Any]:
        self._heading("1/5 Todoist")
        token = self._env.get(TODOIST, "")
        while not (token and (user := self._todoist_user(token))):
            if token:
                self._say("Todoist did not accept that token.")
            self._link("Copy your API token from the Developer tab:", TODOIST_TOKEN_URL)
            token = self._ask("Todoist API token: ", secret=True)
        self._save({TODOIST: token})
        self._say(f"Connected as {user.get('full_name') or user.get('email')}.")
        self._say(
            f"Quick Add reads dates in your Todoist language ({user.get('lang')}), "
            "so speak to Gemini in that language."
        )
        return user

    def _connect_google(self) -> None:
        self._heading("2/5 Google Tasks")
        while True:
            client_id, client_secret = self._env.get(CLIENT_ID), self._env.get(CLIENT_SECRET)
            if not (client_id and client_secret):
                client_id, client_secret = self._ask_google_client()
            refresh_token = self._env.get(REFRESH_TOKEN)
            if not refresh_token:
                # Google answers invalid_grant for a fake token only when the client is right,
                # so a wrong client fails here instead of on Google's sign-in page.
                if self._check_google(client_id, client_secret, "-") == "invalid_client":
                    self._say("Google did not accept that client id or secret.")
                    self._forget(CLIENT_ID, CLIENT_SECRET)
                    continue
                refresh_token = self._sign_in_to_google(client_id, client_secret)
            status = self._check_google(client_id, client_secret, refresh_token)
            if status == "ok":
                self._say("Connected to Google Tasks.")
                return
            if status == "invalid_client":
                self._say("Google did not accept that client id or secret.")
                self._forget(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)
            elif status == "invalid_grant":
                self._say("Your Google sign-in expired or was revoked. Sign in again.")
                self._forget(REFRESH_TOKEN)
            else:
                self._link("Enable the Google Tasks API, then come back:", TASKS_API_URL)
                self._ask("Press Enter when the API is enabled. ")

    def _ask_google_client(self) -> tuple[str, str]:
        self._say("Create a Google OAuth client in the Google Cloud console.")
        self._say(f"Sign in with the Google account your phone uses. Help: {GOOGLE_HELP_URL}")
        for number, (text, url) in enumerate(GOOGLE_STEPS, start=1):
            self._link(f"{number}. {text}", url)
        client_id = self._ask("Google OAuth client id: ")
        client_secret = self._ask("Google OAuth client secret: ", secret=True)
        self._save({CLIENT_ID: client_id, CLIENT_SECRET: client_secret})
        self._forget(REFRESH_TOKEN)
        return client_id, client_secret

    def _sign_in_to_google(self, client_id: str, client_secret: str) -> str:
        self._say("Your browser opens Google's sign-in page.")
        self._say(
            'Google shows "Google hasn\'t verified this app". You created this app, so '
            "choose Advanced, then Go to todoist-bridge, then Continue."
        )
        self._ask("Press Enter to open the browser. ")
        refresh_token = self._obtain_refresh_token(client_id, client_secret)
        self._save({REFRESH_TOKEN: refresh_token})
        return refresh_token

    def _first_sync_is_accepted(self) -> bool:
        self._heading("3/5 First sync preview")
        self._say("Nothing is written yet. These are the changes the first sync makes:")
        stats = self._dry_run(self._env)
        if stats.errors:
            self._say("The preview failed: " + "; ".join(stats.errors))
            return False
        if not any(v for k, v in stats.as_dict().items() if k != "errors"):
            self._say("No changes: Google Tasks and Todoist are already in sync.")
        if not stats.created_in_todoist:
            return True
        answer = self._ask(
            f"The first sync sends {stats.created_in_todoist} open task(s) from Google Tasks "
            "to Todoist. Continue? [y/N] "
        )
        if answer.lower().startswith("y"):
            return True
        self._say(
            "Stopped before the deploy. Complete or delete those tasks in Google Tasks, "
            "then run setup again."
        )
        return False

    def _deploy(self) -> None:
        self._heading("4/5 Modal")
        if not self._modal.logged_in():
            self._link("Create a free Modal account if you have none:", "https://modal.com/signup")
            self._say("Your browser opens Modal to sign in this computer.")
            self._modal.login()
        answer = self._ask("Deploy the sync to Modal now? It runs every minute. [Y/n] ")
        if answer.lower().startswith("n"):
            self._say("Not deployed. Run setup again when you are ready.")
            return
        self._modal.put_secret(self._env)
        self._modal.deploy()
        self._say("Deployed. Read the sync logs with: uv run modal app logs todoist-bridge")

    def _show_gemini_instructions(self, language: str) -> None:
        self._heading("5/5 Gemini")
        self._link("Paste this text into Gemini Saved Info:", SAVED_INFO_URL)
        self._say()
        self._say(saved_info_for(language))
        self._say()
        example = (
            "comprar leite amanhã às 16h"
            if language.lower().startswith("pt")
            else "buy milk tomorrow at 4pm"
        )
        self._say(f'Done. Try it: tell Gemini "{example}" and check Todoist.')

    def _link(self, text: str, url: str) -> None:
        self._say(text)
        self._say(f"  {url}")
        if not self._ask("  Press Enter to open it, or type s to skip: ").lower().startswith("s"):
            self._open_url(url)

    def _save(self, values: Mapping[str, str]) -> None:
        self._env.update(values)
        write_env(self._env_path, values)

    def _forget(self, *keys: str) -> None:
        for key in keys:
            self._env.pop(key, None)
        write_env(self._env_path, dict.fromkeys(keys, ""))

    def _heading(self, text: str) -> None:
        self._say()
        self._say(f"== {text} ==")

    def _ask(self, prompt: str, *, secret: bool = False) -> str:
        return self._console.ask(prompt, secret=secret)

    def _say(self, text: str = "") -> None:
        self._console.say(text)
