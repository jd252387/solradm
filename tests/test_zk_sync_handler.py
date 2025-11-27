import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))


class FailingZK:
    def __init__(self, delete_exception=None):
        self.delete_exception = delete_exception

    def exists(self, path):
        return True

    def delete(self, path, recursive=False):
        if self.delete_exception:
            raise self.delete_exception


class RecordingZK:
    def __init__(self):
        self.delete_calls = []

    def exists(self, path):  # noqa: ARG002
        return True

    def delete(self, path, recursive=False):
        self.delete_calls.append((path, recursive))


@pytest.fixture

def temp_sync_dir(tmp_path):
    base_dir = tmp_path / "sync"
    base_dir.mkdir()
    return base_dir


@pytest.fixture
def handler_cls(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from platformdirs import user_config_dir

    config_path = Path(user_config_dir("solradm", "eclipse")) / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("contexts: {available: [], current: {name: default}}\n")

    for module in [
        "solradm.config",
        "solradm.api",
        "solradm.commands.zk.utils.sync_handler",
    ]:
        sys.modules.pop(module, None)

    sync_handler_module = importlib.import_module("solradm.commands.zk.utils.sync_handler")
    return sync_handler_module.ZooKeeperSyncHandler


def test_sync_reports_create_failure(handler_cls, monkeypatch, capsys, temp_sync_dir):
    module = importlib.import_module(handler_cls.__module__)
    file_path = temp_sync_dir / "configs" / "file.txt"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("content")

    handler = handler_cls(
        zk=object(), temp_dir=str(temp_sync_dir), znode_path="/zk/base", reload=False
    )
    handler.pending_changes[str(file_path)] = "created"

    def failing_create_or_update(*args, **kwargs):  # noqa: ANN001, ANN002
        raise RuntimeError("create failure")

    monkeypatch.setattr(module, "create_or_update", failing_create_or_update)

    handler._sync_changes()

    out = capsys.readouterr().out
    assert "create/update" in out
    assert "/zk/base/configs/file.txt" in out
    assert "create failure" in out
    assert "Sync completed with errors" in out


def test_sync_reports_delete_failure(handler_cls, capsys, temp_sync_dir):
    module = importlib.import_module(handler_cls.__module__)
    file_path = temp_sync_dir / "configs" / "stale.yaml"
    handler = handler_cls(
        zk=FailingZK(delete_exception=RuntimeError("delete failure")),
        temp_dir=str(temp_sync_dir),
        znode_path="/zk/base",
        reload=False,
    )
    handler.pending_changes[str(file_path)] = "deleted"

    handler._sync_changes()

    out = capsys.readouterr().out
    assert "delete" in out
    assert "/zk/base/configs/stale.yaml" in out
    assert "delete failure" in out
    assert "Sync completed with errors" in out


def test_directory_move_syncs_new_paths(handler_cls, monkeypatch, temp_sync_dir):
    module = importlib.import_module(handler_cls.__module__)
    zk = RecordingZK()
    created_paths = []

    def fake_create_or_update(_zk_client, path, data):  # noqa: ANN001, ANN002
        created_paths.append((path, data))

    monkeypatch.setattr(module, "create_or_update", fake_create_or_update)

    old_dir = temp_sync_dir / "configs" / "old"
    old_dir.mkdir(parents=True)
    (old_dir / "a.txt").write_text("A")
    (old_dir / "nested.txt").write_text("B")

    new_dir = temp_sync_dir / "configs" / "new"
    old_dir.rename(new_dir)

    handler = handler_cls(zk=zk, temp_dir=str(temp_sync_dir), znode_path="/zk", reload=False)
    handler.on_moved(SimpleNamespace(src_path=str(old_dir), dest_path=str(new_dir), is_directory=True))
    handler._sync_changes()

    assert zk.delete_calls == [("/zk/configs/old", True)]
    assert {path for path, _ in created_paths} == {
        "/zk/configs/new/a.txt",
        "/zk/configs/new/nested.txt",
    }


def test_move_to_temp_triggers_delete(handler_cls, temp_sync_dir):
    zk = RecordingZK()
    doomed_dir = temp_sync_dir / "configs" / "doomed"
    doomed_dir.mkdir(parents=True)
    temp_dest = temp_sync_dir / "trash" / "doomed"

    handler = handler_cls(zk=zk, temp_dir=str(temp_sync_dir), znode_path="/zk", reload=False)
    handler.on_moved(
        SimpleNamespace(src_path=str(doomed_dir), dest_path=str(temp_dest), is_directory=True)
    )
    handler._sync_changes()

    assert zk.delete_calls == [("/zk/configs/doomed", True)]
