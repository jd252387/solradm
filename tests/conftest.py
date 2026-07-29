import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def isolate_user_config(monkeypatch, tmp_path):
    """Keep tests away from the real user settings file.

    Tests isolate config by setting XDG_CONFIG_HOME, but platformdirs ignores
    that on macOS (it returns ~/Library/Application Support), so any test that
    reaches persist() would clobber the developer's own contexts. Force the
    XDG layout everywhere and default it to a tmp dir.
    """
    import platformdirs

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        platformdirs,
        "user_config_dir",
        lambda appname=None, *a, **k: str(Path(os.environ["XDG_CONFIG_HOME"]) / appname),
    )

    # A missing settings file kicks off the interactive first-run wizard.
    default_config = Path(os.environ["XDG_CONFIG_HOME"]) / "solradm" / "settings.yaml"
    default_config.parent.mkdir(parents=True, exist_ok=True)
    default_config.write_text("contexts:\n  available: []\n  current: {}\n")

    import solradm.config as cfg

    monkeypatch.setattr(cfg, "config_path", default_config)
