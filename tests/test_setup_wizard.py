import contextlib
import stat
from pathlib import Path

import pytest

from todoist_bridge.gemini import SAVED_INFO_EN, SAVED_INFO_PT
from todoist_bridge.setup_wizard import SetupWizard, demo_wizard, read_env, write_env
from todoist_bridge.sync import Stats

README = Path(__file__).parent.parent / "README.md"

WORKING_ENV = {
    "TODOIST_API_TOKEN": "todoist-token",
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "GOOGLE_REFRESH_TOKEN": "refresh-token",
}
USER = {"full_name": "Ana", "lang": "en"}


class ScriptedTerminal:
    """Answers each prompt with the next scripted answer for the first matching key.
    Without a match, ask returns Enter and confirm returns its default."""

    def __init__(self, answers: dict[str, list] | None = None) -> None:
        self._answers = answers or {}
        self.prompts: list[str] = []
        self.output: list[str] = []
        self.links: list[str] = []
        self.rows: list[tuple] = []

    def _scripted(self, prompt: str, fallback):
        self.prompts.append(prompt)
        for key, queue in self._answers.items():
            if key in prompt and queue:
                return queue.pop(0)
        return fallback

    def ask(self, prompt: str, *, secret: bool = False) -> str:
        return self._scripted(prompt, "")

    def confirm(self, prompt: str, *, default: bool) -> bool:
        return self._scripted(prompt, default)

    def welcome(self, title: str, text: str) -> None:
        self.output.append(text)

    def heading(self, step: int, total: int, title: str) -> None:
        self.output.append(title)

    def say(self, text: str = "") -> None:
        self.output.append(text)

    ok = warn = copyable = say

    def link(self, text: str, url: str) -> None:
        self.links.append(url)

    def status(self, text: str):
        return contextlib.nullcontext()

    def summary(self, rows) -> None:
        self.rows = rows


class FakeModal:
    def __init__(self, *, logged_in: bool = True) -> None:
        self.is_logged_in = logged_in
        self.secret: dict[str, str] | None = None
        self.deployed = False

    def logged_in(self) -> bool:
        return self.is_logged_in

    def login(self) -> None:
        self.is_logged_in = True

    def put_secret(self, values):
        self.secret = dict(values)

    def deploy(self) -> None:
        self.deployed = True


@pytest.fixture
def env_path(tmp_path) -> Path:
    return tmp_path / ".env"


def make_wizard(env_path, console, modal=None, **overrides):
    options = {
        "todoist_user": lambda token: USER,
        "check_google": lambda client_id, secret, refresh: "ok",
        "obtain_refresh_token": lambda client_id, secret: "new-refresh-token",
        "dry_run": lambda env: Stats(),
        "open_url": lambda url: None,
        "copy_to_clipboard": lambda text: True,
    }
    options.update(overrides)
    return SetupWizard(env_path, terminal=console, modal=modal or FakeModal(), **options)


def test_working_credentials_skip_their_prompts_and_deploy(env_path):
    write_env(env_path, {**WORKING_ENV, "GOOGLE_TASKLIST": "list-1"})
    console, modal = ScriptedTerminal(), FakeModal()

    exit_code = make_wizard(env_path, console, modal).run()

    assert exit_code == 0
    assert not [p for p in console.prompts if "token" in p or "client" in p]
    assert "No changes: Google Tasks and Todoist are already in sync." in console.output
    assert modal.deployed
    assert modal.secret == {**WORKING_ENV, "GOOGLE_TASKLIST": "list-1"}


def test_rejected_todoist_token_asks_again(env_path):
    write_env(env_path, {k: v for k, v in WORKING_ENV.items() if k != "TODOIST_API_TOKEN"})
    console = ScriptedTerminal({"Todoist API token": ["bad", "good"]})

    make_wizard(env_path, console, todoist_user=lambda t: USER if t == "good" else None).run()

    assert "Todoist did not accept that token." in console.output
    assert read_env(env_path)["TODOIST_API_TOKEN"] == "good"


def test_rejected_google_client_asks_for_the_client_again(env_path):
    write_env(env_path, {"TODOIST_API_TOKEN": "todoist-token"})
    console = ScriptedTerminal(
        {"client id": ["bad-id", "good-id"], "client secret": ["secret", "secret"]}
    )

    def check_google(client_id, secret, refresh):
        if client_id == "bad-id":
            return "invalid_client"
        return "ok" if refresh == "new-refresh-token" else "invalid_grant"

    make_wizard(env_path, console, check_google=check_google).run()

    saved = read_env(env_path)
    assert saved["GOOGLE_CLIENT_ID"] == "good-id"
    assert saved["GOOGLE_REFRESH_TOKEN"] == "new-refresh-token"


def test_expired_google_sign_in_asks_to_sign_in_again(env_path):
    write_env(env_path, WORKING_ENV)
    console = ScriptedTerminal()

    def check_google(client_id, secret, refresh):
        return "ok" if refresh == "new-refresh-token" else "invalid_grant"

    make_wizard(env_path, console, check_google=check_google).run()

    assert read_env(env_path)["GOOGLE_REFRESH_TOKEN"] == "new-refresh-token"


def test_declining_the_import_of_google_tasks_stops_before_deploy(env_path):
    write_env(env_path, WORKING_ENV)
    console, modal = ScriptedTerminal({"Continue?": [False]}), FakeModal()

    exit_code = make_wizard(
        env_path, console, modal, dry_run=lambda env: Stats(created_in_todoist=2)
    ).run()

    assert exit_code == 1
    assert modal.secret is None
    assert not modal.deployed


def test_declining_the_deploy_keeps_the_modal_secret_unchanged(env_path):
    write_env(env_path, WORKING_ENV)
    modal = FakeModal()

    console = ScriptedTerminal({"Deploy": [False]})

    make_wizard(env_path, console, modal).run()

    assert modal.secret is None
    assert not modal.deployed
    assert console.rows[2][0] is False


def test_missing_modal_login_signs_in_before_deploy(env_path):
    write_env(env_path, WORKING_ENV)
    modal = FakeModal(logged_in=False)

    make_wizard(env_path, ScriptedTerminal(), modal).run()

    assert modal.deployed


@pytest.mark.parametrize(("lang", "text"), [("pt_BR", SAVED_INFO_PT), ("en", SAVED_INFO_EN)])
def test_gemini_text_follows_the_todoist_language_and_goes_to_the_clipboard(env_path, lang, text):
    write_env(env_path, WORKING_ENV)
    console, copied = ScriptedTerminal(), []

    make_wizard(
        env_path,
        console,
        todoist_user=lambda t: {**USER, "lang": lang},
        copy_to_clipboard=lambda t: copied.append(t) or True,
    ).run()

    assert text in console.output
    assert copied == [text]


def test_links_open_in_the_browser_unless_declined(env_path):
    write_env(env_path, {k: v for k, v in WORKING_ENV.items() if k != "TODOIST_API_TOKEN"})
    opened: list[str] = []
    console = ScriptedTerminal({"Open it": [True, False], "Todoist API token": ["t"]})

    make_wizard(env_path, console, open_url=opened.append).run()

    assert opened == ["https://app.todoist.com/app/settings/integrations/developer"]
    assert "https://gemini.google.com/saved-info" in console.links


def test_env_file_is_private_and_keeps_unrelated_lines(env_path):
    env_path.write_text("# my settings\nGOOGLE_TASKLIST=list-1\nTODOIST_API_TOKEN=old\n")

    write_env(env_path, {"TODOIST_API_TOKEN": "new", "GOOGLE_CLIENT_ID": "id"})

    assert env_path.read_text() == (
        "# my settings\nGOOGLE_TASKLIST=list-1\nTODOIST_API_TOKEN=new\nGOOGLE_CLIENT_ID=id\n"
    )
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_readme_shows_the_same_saved_info_text_as_the_wizard():
    readme = README.read_text()

    assert SAVED_INFO_PT in readme
    assert SAVED_INFO_EN in readme


def test_demo_accepts_any_value_and_reaches_the_end(env_path):
    console = ScriptedTerminal(
        {"Todoist API token": ["x"], "client id": ["x"], "client secret": ["x"]}
    )

    exit_code = demo_wizard(
        env_path,
        language="pt_BR",
        terminal=console,
        open_url=lambda url: None,
        copy_to_clipboard=lambda text: True,
    ).run()

    assert exit_code == 0
    assert SAVED_INFO_PT in console.output
    assert [mark for mark, _, _ in console.rows] == [True, True, True, None]
