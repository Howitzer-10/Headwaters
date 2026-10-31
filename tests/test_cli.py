from typer.testing import CliRunner

import headwaters
from headwaters.cli.main import app

runner = CliRunner()


def test_help_prints_stub() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Headwaters" in result.output


def test_features_lists_legend_and_hides_example() -> None:
    result = runner.invoke(app, ["features"])
    assert result.exit_code == 0
    assert "★ = free during beta; may become part of Headwaters Pro at 1.0" in result.output
    assert "_example" not in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == headwaters.__version__
