"""``todoist-bridge setup``: collect and check the credentials, preview the first sync,
deploy to Modal and print the Gemini instructions."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import webbrowser
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from todoist_bridge.gemini import SAVED_INFO_URL, saved_info_for
from todoist_bridge.google_tasks import TOKEN_URL, GoogleTasksClient
from todoist_bridge.oauth import obtain_refresh_token
from todoist_bridge.sync import Stats
from todoist_bridge.terminal import RichTerminal, copy_to_clipboard
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
MODAL_SIGNUP_URL = "https://modal.com/signup"


class Terminal(Protocol):
    def welcome(self, title: str, text: str) -> None: ...
    def heading(self, step: int, total: int, title: str) -> None: ...
    def say(self, text: str = "") -> None: ...
    def ok(self, text: str) -> None: ...
    def warn(self, text: str) -> None: ...
    def link(self, text: str, url: str) -> None: ...
    def ask(self, prompt: str, *, secret: bool = False) -> str: ...
    def confirm(self, prompt: str, *, default: bool) -> bool: ...
    def status(self, text: str) -> AbstractContextManager[None]: ...
    def copyable(self, text: str) -> None: ...
    def summary(self, rows: list[tuple[bool | None, str, str]]) -> None: ...


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


STEPS = 5


class SetupWizard:
    def __init__(
        self,
        env_path: Path,
        *,
        terminal: Terminal | None = None,
        todoist_user: Callable[[str], dict[str, Any] | None] = todoist_user,
        check_google: Callable[[str, str, str], GoogleStatus] = check_google,
        obtain_refresh_token: Callable[[str, str], str] = obtain_refresh_token,
        dry_run: Callable[[Mapping[str, str]], Stats],
        modal: Modal,
        open_url: Callable[[str], object] = webbrowser.open,
        copy_to_clipboard: Callable[[str], bool] = copy_to_clipboard,
        welcome_text: str | None = None,
    ) -> None:
        self._welcome_text = welcome_text or (
            "Connects Todoist, Google Tasks, Modal and Gemini.\n"
            f"Each answer is saved in {env_path} right away, so you can stop with "
            "Ctrl+C and run setup again later."
        )
        self._env_path = env_path
        self._terminal = terminal or RichTerminal()
        self._todoist_user = todoist_user
        self._check_google = check_google
        self._obtain_refresh_token = obtain_refresh_token
        self._dry_run = dry_run
        self._modal = modal
        self._open_url = open_url
        self._copy_to_clipboard = copy_to_clipboard
        self._env: dict[str, str] = {}

    def run(self) -> int:
        self._env = read_env(self._env_path)
        self._terminal.welcome("todoist-bridge setup", self._welcome_text)
        user = self._connect_todoist()
        self._connect_google()
        if not self._first_sync_is_accepted():
            return 1
        deployed = self._deploy()
        language = user.get("lang") or "en"
        copied = self._show_gemini_instructions(language)
        self._terminal.summary(
            [
                (True, "Todoist", f"connected as {_display_name(user)}"),
                (True, "Google Tasks", "connected"),
                (
                    deployed,
                    "Modal",
                    "deployed, syncs every minute"
                    if deployed
                    else "not deployed, run setup again to deploy",
                ),
                (
                    None,
                    "Gemini",
                    "paste the text into Saved Info"
                    + (" (it is in your clipboard)" if copied else ""),
                ),
            ]
        )
        self._terminal.say(f'Try it: tell Gemini "{_example(language)}" and check Todoist.')
        return 0

    def _connect_todoist(self) -> dict[str, Any]:
        self._terminal.heading(1, STEPS, "Todoist")
        token = self._env.get(TODOIST, "")
        while True:
            if token:
                with self._terminal.status("Checking the Todoist token..."):
                    user = self._todoist_user(token)
                if user:
                    break
                self._terminal.warn("Todoist did not accept that token.")
            self._offer_link("Copy your API token from the Developer tab:", TODOIST_TOKEN_URL)
            token = self._terminal.ask("Todoist API token", secret=True)
        self._save({TODOIST: token})
        self._terminal.ok(f"Connected as {_display_name(user)}.")
        self._terminal.say(
            f"  Quick Add reads dates in your Todoist language ([bold]{user.get('lang')}[/]), "
            "so speak to Gemini in that language."
        )
        return user

    def _connect_google(self) -> None:
        self._terminal.heading(2, STEPS, "Google Tasks")
        while True:
            client_id, client_secret = self._env.get(CLIENT_ID), self._env.get(CLIENT_SECRET)
            if not (client_id and client_secret):
                client_id, client_secret = self._ask_google_client()
            refresh_token = self._env.get(REFRESH_TOKEN)
            if not refresh_token:
                # Google answers invalid_grant for a fake token only when the client is right,
                # so a wrong client fails here instead of on Google's sign-in page.
                with self._terminal.status("Checking the OAuth client..."):
                    status = self._check_google(client_id, client_secret, "-")
                if status == "invalid_client":
                    self._terminal.warn("Google did not accept that client id or secret.")
                    self._forget(CLIENT_ID, CLIENT_SECRET)
                    continue
                refresh_token = self._sign_in_to_google(client_id, client_secret)
            with self._terminal.status("Checking Google Tasks..."):
                status = self._check_google(client_id, client_secret, refresh_token)
            if status == "ok":
                self._terminal.ok("Connected to Google Tasks.")
                return
            if status == "invalid_client":
                self._terminal.warn("Google did not accept that client id or secret.")
                self._forget(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)
            elif status == "invalid_grant":
                self._terminal.warn("Your Google sign-in expired or was revoked. Sign in again.")
                self._forget(REFRESH_TOKEN)
            else:
                self._terminal.warn("The Google Tasks API is not enabled in your project.")
                self._offer_link("Enable it, then come back:", TASKS_API_URL)
                self._terminal.ask("Press Enter when the API is enabled")

    def _ask_google_client(self) -> tuple[str, str]:
        self._terminal.say(
            "Create a Google OAuth client in the Google Cloud console. Sign in with the "
            "Google account your phone uses."
        )
        self._terminal.say(f"  [dim]Help: {GOOGLE_HELP_URL}[/]")
        for number, (text, url) in enumerate(GOOGLE_STEPS, start=1):
            self._terminal.say()
            self._offer_link(f"[bold]{number}.[/] {text}", url)
        self._terminal.say()
        client_id = self._terminal.ask("Google OAuth client id")
        client_secret = self._terminal.ask("Google OAuth client secret", secret=True)
        self._save({CLIENT_ID: client_id, CLIENT_SECRET: client_secret})
        self._forget(REFRESH_TOKEN)
        return client_id, client_secret

    def _sign_in_to_google(self, client_id: str, client_secret: str) -> str:
        self._terminal.say("Your browser opens Google's sign-in page.")
        self._terminal.warn(
            "Google shows [bold]Google hasn't verified this app[/]. You created this app, "
            "so choose Advanced, then Go to todoist-bridge, then Continue."
        )
        self._terminal.ask("Press Enter to open the browser")
        refresh_token = self._obtain_refresh_token(client_id, client_secret)
        self._save({REFRESH_TOKEN: refresh_token})
        return refresh_token

    def _first_sync_is_accepted(self) -> bool:
        self._terminal.heading(3, STEPS, "First sync preview")
        self._terminal.say("[dim]Nothing is written yet. The first sync makes these changes:[/]")
        with self._terminal.status("Reading Google Tasks and Todoist..."):
            stats = self._dry_run(self._env)
        if stats.errors:
            self._terminal.warn("The preview failed: " + "; ".join(stats.errors))
            return False
        if not any(v for k, v in stats.as_dict().items() if k != "errors"):
            self._terminal.ok("No changes: Google Tasks and Todoist are already in sync.")
        if not stats.created_in_todoist:
            return True
        if self._terminal.confirm(
            f"The first sync sends {stats.created_in_todoist} open task(s) from Google Tasks "
            "to Todoist. Continue?",
            default=False,
        ):
            return True
        self._terminal.warn(
            "Stopped before the deploy. Complete or delete those tasks in Google Tasks, "
            "then run setup again."
        )
        return False

    def _deploy(self) -> bool:
        self._terminal.heading(4, STEPS, "Modal")
        if not self._modal.logged_in():
            self._offer_link("Create a free Modal account if you have none:", MODAL_SIGNUP_URL)
            self._terminal.say("Your browser opens Modal to sign in this computer.")
            self._modal.login()
        if not self._terminal.confirm(
            "Deploy the sync to Modal now? It runs every minute.", default=True
        ):
            self._terminal.warn("Not deployed. Run setup again when you are ready.")
            return False
        self._modal.put_secret(self._env)
        self._modal.deploy()
        self._terminal.ok("Deployed.")
        self._terminal.say("  [dim]Sync logs: uv run modal app logs todoist-bridge[/]")
        return True

    def _show_gemini_instructions(self, language: str) -> bool:
        self._terminal.heading(5, STEPS, "Gemini")
        text = saved_info_for(language)
        self._terminal.say("Paste this text into Gemini Saved Info:")
        self._terminal.copyable(text)
        copied = self._copy_to_clipboard(text)
        if copied:
            self._terminal.ok("Copied to your clipboard.")
        self._offer_link("Open Gemini Saved Info:", SAVED_INFO_URL)
        return copied

    def _offer_link(self, text: str, url: str) -> None:
        self._terminal.link(text, url)
        if self._terminal.confirm("Open it in your browser?", default=True):
            self._open_url(url)

    def _save(self, values: Mapping[str, str]) -> None:
        self._env.update(values)
        write_env(self._env_path, values)

    def _forget(self, *keys: str) -> None:
        for key in keys:
            self._env.pop(key, None)
        write_env(self._env_path, dict.fromkeys(keys, ""))


class DemoModal:
    """Pretends this computer has no Modal login, so the demo shows the sign-up step."""

    def logged_in(self) -> bool:
        return False

    def login(self) -> None:
        pass

    def put_secret(self, values: Mapping[str, str]) -> None:
        pass

    def deploy(self) -> None:
        pass


def _demo_dry_run(env: Mapping[str, str]) -> Stats:
    print("[dry run] would insert Google task: title='Pay the electricity bill'")
    print("[dry run] would insert Google task: title='Call the dentist'")
    return Stats(created_in_gtasks=2)


def demo_wizard(env_path: Path, *, language: str, **options: Any) -> SetupWizard:
    """A wizard that accepts any value and never calls Todoist, Google or Modal."""
    return SetupWizard(
        env_path,
        todoist_user=lambda token: {"full_name": "Demo User", "lang": language},
        check_google=lambda client_id, secret, refresh_token: (
            "invalid_grant" if refresh_token == "-" else "ok"
        ),
        obtain_refresh_token=lambda client_id, secret: "demo-refresh-token",
        dry_run=_demo_dry_run,
        modal=DemoModal(),
        welcome_text=(
            "[bold yellow]Demo mode.[/] Any value works, and nothing reaches Todoist, "
            "Google or Modal.\nYour answers go to a temporary file that is deleted at the end."
        ),
        **options,
    )


def _display_name(user: Mapping[str, Any]) -> str:
    return user.get("full_name") or user.get("email") or "your Todoist account"


def _example(language: str) -> str:
    if language.lower().startswith("pt"):
        return "comprar leite amanhã às 16h"
    return "buy milk tomorrow at 4pm"
