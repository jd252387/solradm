from pathlib import Path

from typer.testing import CliRunner

from test_zk_upload_filters import _import_editor


class FakeZK:
    def __init__(self, tree: dict[str, dict]):
        self.tree = tree

    def exists(self, path: str) -> bool:
        return path in self.tree

    def get_children(self, path: str):
        return list(self.tree.get(path, {}).get("children", []))

    def get(self, path: str):
        node = self.tree[path]
        return node.get("data", b""), None



def test_zoo_diff_renders_rich_differences(monkeypatch, tmp_path: Path):
    editor = _import_editor(monkeypatch)

    configsets_dir = tmp_path / "configsets"
    managed_schema = configsets_dir / "alpha" / "conf" / "managed-schema"
    managed_schema.parent.mkdir(parents=True)
    managed_schema.write_text("local-content")

    tree = {
        "/configs": {"children": ["alpha"]},
        "/configs/alpha": {"children": ["conf"], "data": b""},
        "/configs/alpha/conf": {"children": ["managed-schema"], "data": b""},
        "/configs/alpha/conf/managed-schema": {"children": [], "data": b"remote-content"},
    }
    fake_client = FakeZK(tree)

    monkeypatch.setattr(editor, "get_client", lambda: fake_client)
    monkeypatch.setattr(editor, "get_default_configsets_config_dir", lambda: configsets_dir)

    runner = CliRunner()
    result = runner.invoke(editor.app, ["diff", "alpha"])

    assert result.exit_code == 0
    assert "managed-schema" in result.stdout
    assert "local-content" in result.stdout
    assert "remote-content" in result.stdout
