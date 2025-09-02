import importlib
from pathlib import Path

import pytest
import typer


def _prepare(monkeypatch, tmp_path: Path, repo_content: str, settings_content: str):
    repo = tmp_path / "repo.yaml"
    repo.write_text(repo_content)
    config_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    cfg_dir = config_home / "solradm"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "settings.yaml").write_text(settings_content)
    import solradm.config as cfg
    importlib.reload(cfg)
    from solradm.commands import config as config_cmd
    importlib.reload(config_cmd)
    return repo, cfg, config_cmd


def test_merge_and_precedence(monkeypatch, tmp_path):
    repo_content = """contexts:
  available:
    - name: repo
      zk: rzk
    - name: dup
      zk: repo_dup
"""
    settings_content = """context_repositories:
  - {repo}
contexts:
  available:
    - name: dup
      zk: local_dup
  current: {{name: dup}}
"""
    repo, cfg, _ = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content.format(repo=tmp_path / "repo.yaml"),
    )

    names = {c["name"]: c["zk"] for c in cfg.settings.contexts.available}
    assert names["repo"] == "rzk"
    assert names["dup"] == "local_dup"


def test_add_and_delete_repo(monkeypatch, tmp_path):
    repo_content = """contexts:
  available:
    - name: r1
      zk: z1
"""
    settings_content = """contexts:
  available: []
  current: {}
"""
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content,
    )

    config_cmd.add_repo(repo)
    importlib.reload(cfg)
    importlib.reload(config_cmd)
    assert str(repo) in cfg.settings.get("context_repositories")
    assert "r1" in [c["name"] for c in cfg.settings.contexts.available]

    config_cmd.del_repo(repo)
    importlib.reload(cfg)
    importlib.reload(config_cmd)
    import yaml
    with open(cfg.config_path) as f:
        data = yaml.safe_load(f) or {}
    assert str(repo) not in data.get("context_repositories", [])


def test_add_repo_invalid(monkeypatch, tmp_path):
    repo_content = """contexts:\n  available: []\n"""
    settings_content = """contexts:\n  available: []\n  current: {}\n"""
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content,
    )

    bad_repo = tmp_path / "bad.yaml"
    bad_repo.write_text("bad: true")
    with pytest.raises(typer.BadParameter):
        config_cmd.add_repo(bad_repo)
