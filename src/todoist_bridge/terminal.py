"""Terminal output for the setup wizard."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

_CLIPBOARD_COMMANDS = (
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
    ["pbcopy"],
    ["clip"],
)


class RichTerminal:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console(highlight=False)

    def welcome(self, title: str, text: str) -> None:
        self._console.print(Panel.fit(text, title=f"[bold]{title}[/]", border_style="cyan"))

    def heading(self, step: int, total: int, title: str) -> None:
        self._console.print()
        self._console.rule(f"[bold cyan]Step {step} of {total}  {title}[/]", align="left")

    def say(self, text: str = "") -> None:
        self._console.print(text)

    def ok(self, text: str) -> None:
        self._console.print(f"[green]✓[/] {text}")

    def warn(self, text: str) -> None:
        self._console.print(f"[yellow]![/] {text}")

    def link(self, text: str, url: str) -> None:
        self._console.print(f"[bold]→[/] {text}")
        self._console.print(f"  [link={url}][blue underline]{url}[/][/link]", soft_wrap=True)

    def ask(self, prompt: str, *, secret: bool = False) -> str:
        hint = " [dim](hidden while you type)[/]" if secret else ""
        return Prompt.ask(f"  {prompt}{hint}", password=secret, console=self._console).strip()

    def confirm(self, prompt: str, *, default: bool) -> bool:
        return Confirm.ask(f"  {prompt}", default=default, console=self._console)

    @contextmanager
    def status(self, text: str) -> Iterator[None]:
        with self._console.status(text):
            yield

    def copyable(self, text: str) -> None:
        """Print text without inserted line breaks, so a terminal copy keeps one paragraph."""
        self._console.print()
        self._console.print(text, soft_wrap=True, style="bold")
        self._console.print()

    def summary(self, rows: list[tuple[bool | None, str, str]]) -> None:
        table = Table(show_header=False, box=None, padding=(0, 2))
        marks = {True: "[green]✓[/]", False: "[red]✗[/]", None: "[yellow]-[/]"}
        for done, name, detail in rows:
            table.add_row(marks[done], f"[bold]{name}[/]", detail)
        self._console.print()
        self._console.print(Panel(table, title="[bold]Setup summary[/]", border_style="green"))


def copy_to_clipboard(text: str) -> bool:
    for command in _CLIPBOARD_COMMANDS:
        if shutil.which(command[0]):
            result = subprocess.run(command, input=text.encode(), check=False)
            return result.returncode == 0
    return False
