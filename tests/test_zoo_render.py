import importlib
import sys
from pathlib import Path


def _load_render_jinja_tree(monkeypatch, tmp_path: Path):
    cfg_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    settings_path = cfg_home / "solradm" / "settings.yaml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("contexts: {available: [], current: {name: default}}\n", encoding="utf-8")

    for module in [
        "solradm.config",
        "solradm.api",
        "solradm.commands.zk.editor",
    ]:
        sys.modules.pop(module, None)

    editor = importlib.import_module("solradm.commands.zk.editor")
    return editor._render_jinja_tree


def _load_editor_module(monkeypatch, tmp_path: Path):
    cfg_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    settings_path = cfg_home / "solradm" / "settings.yaml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("contexts: {available: [], current: {name: default}}\n", encoding="utf-8")

    for module in [
        "solradm.config",
        "solradm.api",
        "solradm.commands.zk.editor",
    ]:
        sys.modules.pop(module, None)

    return importlib.import_module("solradm.commands.zk.editor")


def test_render_jinja_tree_creates_rendered_output(monkeypatch, tmp_path: Path):
    render_jinja_tree = _load_render_jinja_tree(monkeypatch, tmp_path)
    templates = tmp_path / "workspace" / "jinja" / "templates"
    configs = tmp_path / "workspace" / "jinja" / "configs"
    resources = tmp_path / "workspace" / "jinja" / "resources"

    templates.mkdir(parents=True)
    resources.mkdir(parents=True)
    (templates / "base.j2").write_text("header\n{% block body %}{% endblock %}\n", encoding="utf-8")
    (resources / "shared.txt").write_text("shared-resource\n", encoding="utf-8")
    (resources / "scripts").mkdir()
    (resources / "scripts" / "init.sh").write_text("#!/bin/sh\necho init\n", encoding="utf-8")

    env_a = configs / "env-a"
    env_a.mkdir(parents=True)
    (env_a / "app.conf").write_text(
        '{% extends "base.j2" %}\n{% block body %}app=alpha{% endblock %}\n',
        encoding="utf-8",
    )
    nested = env_a / "nested"
    nested.mkdir()
    (nested / "worker.conf").write_text("threads=4\n", encoding="utf-8")

    env_b = configs / "env-b"
    env_b.mkdir(parents=True)
    (env_b / "app.conf").write_text("app=beta\n", encoding="utf-8")

    rendered_dir, rendered_files = render_jinja_tree(tmp_path / "workspace")

    assert rendered_dir == tmp_path / "workspace" / "rendered"
    assert {path.relative_to(rendered_dir).as_posix() for path in rendered_files} == {
        "env-a/app.conf",
        "env-a/nested/worker.conf",
        "env-a/scripts/init.sh",
        "env-a/shared.txt",
        "env-b/app.conf",
        "env-b/scripts/init.sh",
        "env-b/shared.txt",
    }
    assert (rendered_dir / "env-a" / "app.conf").read_text(encoding="utf-8") == "header\napp=alpha"
    assert (rendered_dir / "env-a" / "nested" / "worker.conf").read_text(encoding="utf-8") == "threads=4"
    assert (rendered_dir / "env-a" / "shared.txt").read_text(encoding="utf-8") == "shared-resource\n"
    assert (rendered_dir / "env-b" / "scripts" / "init.sh").read_text(encoding="utf-8") == "#!/bin/sh\necho init\n"
    assert (rendered_dir / "env-b" / "app.conf").read_text(encoding="utf-8") == "app=beta"


def test_render_jinja_tree_replaces_previous_output(monkeypatch, tmp_path: Path):
    render_jinja_tree = _load_render_jinja_tree(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    templates = workspace / "jinja" / "templates"
    configs = workspace / "jinja" / "configs" / "dev"
    templates.mkdir(parents=True)
    configs.mkdir(parents=True)
    (configs / "site.txt").write_text("first\n", encoding="utf-8")

    rendered_dir, _ = render_jinja_tree(workspace)
    stale_file = rendered_dir / "stale.txt"
    stale_file.write_text("old\n", encoding="utf-8")

    (configs / "site.txt").write_text("second\n", encoding="utf-8")
    render_jinja_tree(workspace)

    assert not stale_file.exists()
    assert (rendered_dir / "dev" / "site.txt").read_text(encoding="utf-8") == "second"


def test_prepare_upload_paths_renders_workspace_for_configs(monkeypatch, tmp_path: Path):
    editor = _load_editor_module(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    templates = workspace / "jinja" / "templates"
    configs = workspace / "jinja" / "configs"
    resources = workspace / "jinja" / "resources"

    templates.mkdir(parents=True)
    resources.mkdir(parents=True)
    (resources / "shared.txt").write_text("shared\n", encoding="utf-8")

    dev = configs / "dev"
    dev.mkdir(parents=True)
    (dev / "solrconfig.xml").write_text("dev\n", encoding="utf-8")

    prod = configs / "prod"
    prod.mkdir(parents=True)
    (prod / "solrconfig.xml").write_text("prod\n", encoding="utf-8")

    prepared_paths = editor._prepare_upload_paths(
        [workspace],
        znode_path="/configs",
        no_render=False,
    )

    assert [path.name for path in prepared_paths] == ["dev", "prod"]
    assert (workspace / "rendered" / "dev" / "shared.txt").read_text(encoding="utf-8") == "shared\n"


def test_prepare_upload_paths_can_skip_render(monkeypatch, tmp_path: Path):
    editor = _load_editor_module(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "jinja" / "templates").mkdir(parents=True)
    (workspace / "jinja" / "configs" / "dev").mkdir(parents=True)

    prepared_paths = editor._prepare_upload_paths(
        [workspace],
        znode_path="/configs",
        no_render=True,
    )

    assert prepared_paths == [workspace]
    assert not (workspace / "rendered").exists()


def test_render_uses_default_configsets_directory_when_path_is_omitted(monkeypatch, tmp_path: Path):
    editor = _load_editor_module(monkeypatch, tmp_path)
    default_dir = tmp_path / "configsets"
    default_dir.mkdir()

    calls: list[Path] = []

    monkeypatch.setattr(editor, "get_default_configsets_config_dir", lambda: default_dir)
    monkeypatch.setattr(
        editor,
        "_render_jinja_tree",
        lambda path: (calls.append(path) or (path / "rendered", [])),
    )

    editor.render()

    assert calls == [default_dir]


def test_render_fails_when_default_configsets_directory_is_not_configured(monkeypatch, tmp_path: Path):
    import pytest
    import typer

    editor = _load_editor_module(monkeypatch, tmp_path)
    monkeypatch.setattr(editor, "get_default_configsets_config_dir", lambda: None)

    with pytest.raises(typer.Exit) as exc:
        editor.render()

    assert exc.value.exit_code == 1
