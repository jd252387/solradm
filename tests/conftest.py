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


# Command modules in solradm.commands.kube, in registration order.
KUBE_COMMAND_MODULES = ("logs", "disk", "suspend", "resume", "state_dir", "ui", "list_resources")


@pytest.fixture
def kube_modules(monkeypatch, tmp_path):
    """Fresh kube subapp plus every command module, state dir pointed at tmp_path.

    The kube package is one module per command sharing `subapp.app`, so
    reloading a single command module would register a *second* copy of that
    command on the existing app. Rebuild the app first, then re-register the
    commands onto it.

    Returns a namespace of the reloaded modules (`.suspend`, `.list_resources`,
    ...) plus `.app` for CliRunner.
    """
    import importlib
    from types import SimpleNamespace

    from solradm.commands.kube import subapp

    importlib.reload(subapp)

    modules = {}
    for name in ("common",) + KUBE_COMMAND_MODULES:
        modules[name] = importlib.reload(importlib.import_module(f"solradm.commands.kube.{name}"))
    importlib.reload(importlib.import_module("solradm.commands.kube"))

    for name in ("suspend", "resume", "state_dir"):
        monkeypatch.setattr(modules[name], "_ensure_state_dir", lambda: tmp_path)

    return SimpleNamespace(app=subapp.app, **modules)
