import os

from solradm.completion import configs


class _FakeZK:
    def exists(self, path: str) -> bool:  # pragma: no cover - simple stub
        return False

    def get_children(self, path: str):  # pragma: no cover - simple stub
        return []


def test_config_names_handles_missing_configs(monkeypatch):
    monkeypatch.setattr("solradm.zk.get_client", lambda: _FakeZK())

    assert configs.config_names(None, [], "") == []


def test_config_names_returns_znode_children(monkeypatch):
    class _ZKWithChildren(_FakeZK):
        def exists(self, path: str) -> bool:
            return True

        def get_children(self, path: str):
            return ["alpha", "beta"]

    monkeypatch.setattr("solradm.zk.get_client", lambda: _ZKWithChildren())

    assert configs.config_names(None, [], "b") == ["beta"]


def test_config_names_or_paths_includes_default_configsets(monkeypatch, tmp_path):
    configsets_dir = tmp_path / "configsets"
    configsets_dir.mkdir()
    (configsets_dir / "alpha").mkdir()
    (configsets_dir / "beta").mkdir()

    monkeypatch.setattr(
        "solradm.config.util.get_default_configsets_config_dir",
        lambda: configsets_dir,
    )
    monkeypatch.setattr("solradm.zk.get_client", lambda: _FakeZK())

    suggestions = configs.config_names_or_paths(None, [], "b")

    assert "beta" in suggestions


def test_config_names_or_paths_includes_paths(monkeypatch, tmp_path):
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    (tmp_path / "file.txt").write_text("content")

    monkeypatch.setattr(
        "solradm.config.util.get_default_configsets_config_dir", lambda: None
    )
    monkeypatch.setattr("solradm.zk.get_client", lambda: _FakeZK())

    suggestions = configs.config_names_or_paths(None, [], str(tmp_path))

    assert any(str(local_dir) in suggestion for suggestion in suggestions)


def test_config_names_or_paths_missing_parent(monkeypatch):
    missing_path = os.path.join(os.sep, "tmp", "nonexistent", "config")

    monkeypatch.setattr(
        "solradm.config.util.get_default_configsets_config_dir", lambda: None
    )
    monkeypatch.setattr("solradm.zk.get_client", lambda: _FakeZK())

    assert configs.config_names_or_paths(None, [], missing_path) == []
