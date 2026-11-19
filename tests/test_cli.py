from datetime import UTC, datetime

from fakes import FakeGoogleTasks, FakeTodoist

from todoist_bridge.cli import DryRunGoogleTasks, DryRunTodoist
from todoist_bridge.sync import sync


def test_dry_run_reports_planned_writes_without_sending_them(capsys):
    google, todoist = FakeGoogleTasks(), FakeTodoist()
    google.add("me lembra de comprar leite hoje")
    todoist.add("pagar boleto", due="2026-11-19")

    stats = sync(DryRunGoogleTasks(google), DryRunTodoist(todoist), datetime.now(UTC))

    assert google.writes == []
    assert todoist.writes == []
    assert stats.created_in_todoist == 1
    assert stats.created_in_gtasks == 1
    output = capsys.readouterr().out
    assert "would quick add to Todoist: text='comprar leite hoje'" in output
    assert "would insert Google task: title='pagar boleto'" in output
