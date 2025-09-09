from pathlib import Path

from typer.testing import CliRunner


def test_version_command(tmp_path, monkeypatch):
    cfg_home = tmp_path / "cfg"
    settings = cfg_home / "solradm" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text("contexts:\n  available: []\n  current: {}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))

    from solradm import __version__
    from solradm.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
