import importlib
import sys
import types
from pathlib import Path

import pytest
import typer


def _import_editor(monkeypatch):
    api_module = types.ModuleType("solradm.api")
    api_module.get_initialized_session = lambda: types.SimpleNamespace(close=lambda: None)

    api_state_module = types.ModuleType("solradm.api.state")
    api_state_module.get_collections = lambda: []

    api_utils_module = types.ModuleType("solradm.api.utils")
    api_utils_module.get_collections_using_config = lambda *_args, **_kwargs: {}

    api_module.state = api_state_module
    api_module.utils = api_utils_module

    monkeypatch.setitem(sys.modules, "solradm.api", api_module)
    monkeypatch.setitem(sys.modules, "solradm.api.state", api_state_module)
    monkeypatch.setitem(sys.modules, "solradm.api.utils", api_utils_module)

    sync_handler_module = types.ModuleType("solradm.commands.zk.utils.sync_handler")

    class _SyncHandler:  # pragma: no cover - stub
        def __init__(self, *_args, **_kwargs):
            self.pending_changes = False

        def _sync_changes(self):
            return None

    sync_handler_module.ZooKeeperSyncHandler = _SyncHandler

    monkeypatch.setitem(sys.modules, "solradm.commands.zk.utils.sync_handler", sync_handler_module)

    znode_copier_module = types.ModuleType("solradm.commands.zk.utils.znode_copier")
    znode_copier_module.copy_znode_to_local = lambda *_args, **_kwargs: False

    monkeypatch.setitem(sys.modules, "solradm.commands.zk.utils.znode_copier", znode_copier_module)

    config_module = types.ModuleType("solradm.config")
    config_module.__path__ = []  # type: ignore[attr-defined]
    settings = types.SimpleNamespace(
        auth=types.SimpleNamespace(user="", password=""),
        contexts=types.SimpleNamespace(current=types.SimpleNamespace(name="default", zk="")),
    )
    config_module.settings = settings

    util_module = types.ModuleType("solradm.config.util")

    def _resolve_config_name(path: str):
        target = Path(path)
        if not target.exists():
            raise typer.Exit(1)
        return target

    util_module.resolve_config_name_to_abs_or_default_directory = _resolve_config_name
    util_module.get_current_context = lambda: types.SimpleNamespace(zk="")
    config_module.util = util_module
    monkeypatch.setitem(sys.modules, "solradm.config", config_module)
    monkeypatch.setitem(sys.modules, "solradm.config.util", util_module)

    aiohttp = types.ModuleType("aiohttp")

    class _ClientSession:  # pragma: no cover - stub
        def __init__(self, *_, **__):
            self.closed = False

        async def close(self):
            self.closed = True

    class _BasicAuth:  # pragma: no cover - stub
        def __init__(self, *_, **__):
            return None

    aiohttp.ClientSession = _ClientSession
    aiohttp.BasicAuth = _BasicAuth

    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)

    watchdog = types.ModuleType("watchdog")
    observers = types.ModuleType("watchdog.observers")

    class _Observer:  # pragma: no cover - simple stub
        def schedule(self, *_, **__):
            return None

        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    observers.Observer = _Observer
    watchdog.observers = observers

    monkeypatch.setitem(sys.modules, "watchdog", watchdog)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observers)

    import solradm.commands.zk.editor as editor

    return importlib.reload(editor)


def _patch_upload_dependencies(monkeypatch):
    editor = _import_editor(monkeypatch)
    uploaded = []

    def _fake_create_or_update(client, zk_path, data):
        uploaded.append((zk_path, data))

    monkeypatch.setattr(editor, "create_or_update", _fake_create_or_update)
    monkeypatch.setattr(editor, "get_client", lambda: "client")

    return editor, uploaded


def test_upload_applies_include_and_exclude_patterns(monkeypatch, tmp_path):
    editor, uploaded = _patch_upload_dependencies(monkeypatch)

    base = tmp_path / "configs"
    (base / "keep").mkdir(parents=True)
    (base / "keep" / "match.txt").write_text("ok")
    (base / "keep" / "secret.txt").write_text("nope")
    (base / "ignore").mkdir()
    (base / "ignore" / "match.txt").write_text("ignored")
    (base / "other" / "notes.md").parent.mkdir(parents=True)
    (base / "other" / "notes.md").write_text("md")

    editor.upload(
        paths=[str(base)],
        znode_path="/target",
        include=[r"\.txt$"],
        exclude=[r"^ignore/", r"secret"],
        only_used=False,
        reload=False,
        skip_checks=True,
    )

    assert [path for path, _ in uploaded] == ["/target/keep/match.txt"]


def test_upload_surfaces_invalid_include_pattern(monkeypatch, tmp_path):
    editor, _ = _patch_upload_dependencies(monkeypatch)
    file_path = tmp_path / "file.txt"
    file_path.write_text("data")

    with pytest.raises(typer.BadParameter):
        editor.upload(
            paths=[str(file_path)],
            znode_path="/target",
            include=["["],
            only_used=False,
            reload=False,
            skip_checks=True,
        )
