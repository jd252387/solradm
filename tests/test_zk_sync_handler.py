import importlib
import sys
from pathlib import Path

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
