import sys
import types
from dataclasses import dataclass

sys.modules.setdefault(
    "solradm.api",
    types.SimpleNamespace(get_initialized_session=lambda: types.SimpleNamespace(close=lambda: None)),
)
sys.modules.setdefault(
    "solradm.api.state", types.SimpleNamespace(get_collections=lambda: [])
)
sys.modules.setdefault(
    "solradm.api.utils",
    types.SimpleNamespace(get_collections_using_config=lambda collections, name: []),
)
sys.modules.setdefault(
    "solradm.commands.collections.maintenance",
    types.SimpleNamespace(reload=lambda **kwargs: None),
)

from solradm.commands.zk.utils.sync_handler import ZooKeeperSyncHandler


class FakeKazoo:
    def __init__(self):
        self.data = {}

    def exists(self, path):
        return path in self.data or any(key.startswith(f"{path.rstrip('/')}/") for key in self.data)

    def create(self, path, value, makepath=False):
        self.data[path] = value

    def set(self, path, value):
        self.data[path] = value

    def delete(self, path, recursive=False):
        if recursive:
            for key in list(self.data.keys()):
                if key == path or key.startswith(f"{path.rstrip('/')}/"):
                    self.data.pop(key, None)
        else:
            self.data.pop(path, None)


@dataclass
class DummyEvent:
    src_path: str
    is_directory: bool
    dest_path: str | None = None


def _build_handler(tmp_path):
    temp_dir = tmp_path / "session"
    temp_dir.mkdir()
    zk = FakeKazoo()
    handler = ZooKeeperSyncHandler(zk, str(temp_dir), "/configs", sync_interval=0, reload=False)
    return handler, temp_dir, zk


def test_directory_rename_moves_znode_recursively(tmp_path):
    handler, temp_dir, zk = _build_handler(tmp_path)

    old_dir = temp_dir / "configs" / "old"
    old_dir.mkdir(parents=True)
    file_path = old_dir / "file.txt"
    file_path.write_text("hello")

    zk.create("/configs/old/file.txt", b"stale", makepath=True)

    new_dir = temp_dir / "configs" / "new"
    old_dir.rename(new_dir)

    handler.on_moved(DummyEvent(str(old_dir), is_directory=True, dest_path=str(new_dir)))

    assert "/configs/old/file.txt" not in zk.data
    assert zk.data["/configs/new/file.txt"] == b"hello"


def test_move_out_of_scope_treated_as_delete(tmp_path):
    handler, temp_dir, zk = _build_handler(tmp_path)

    doomed_dir = temp_dir / "configs" / "gone"
    doomed_dir.mkdir(parents=True)
    doomed_file = doomed_dir / "file.txt"
    doomed_file.write_text("bye")
    zk.create("/configs/gone/file.txt", b"bye", makepath=True)

    outside = tmp_path / "trash" / "gone"
    outside.parent.mkdir()
    doomed_dir.rename(outside)

    handler.on_moved(DummyEvent(str(doomed_dir), is_directory=True, dest_path=str(outside)))

    assert not zk.exists("/configs/gone")
