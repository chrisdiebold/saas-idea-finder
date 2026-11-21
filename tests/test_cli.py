from typer.testing import CliRunner

from ideafinder.cli import app
from tests.conftest import UNREACHABLE_URL

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("up", "web", "worker", "doctor", "db"):
        assert command in result.output


def test_up_refuses_to_start_without_database(use_settings):
    use_settings(UNREACHABLE_URL)
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1
    assert "Postgres.app" in result.output


def test_unknown_queue_rejected(use_settings, test_database_url):
    from ideafinder.db import admin

    admin.create_database_if_missing(use_settings(test_database_url))
    admin.upgrade(use_settings(test_database_url))
    result = runner.invoke(app, ["worker", "--queue", "nope"])
    assert result.exit_code != 0
    assert "unknown queue" in result.output
