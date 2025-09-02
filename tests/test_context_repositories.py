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

    config_cmd.remove_repo(repo)
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


def test_upload_and_edit_delete_repo_context(monkeypatch, tmp_path):
    repo_content = """contexts:\n  available: []\n"""
    settings_content = """context_repositories:\n  - {repo}\ncontexts:\n  available:\n    - name: local\n      zk: lzk\n  current: {{name: local}}\n"""
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content.format(repo=tmp_path / "repo.yaml"),
    )

    # upload local context to repo
    config_cmd.upload("local", repo)
    import yaml
    data = yaml.safe_load(repo.read_text())
    assert any(c["name"] == "local" for c in data["contexts"]["available"])

    # edit context - should modify local configuration first
    config_cmd.edit("local", zk="newzk", kubecontext=None)
    data = yaml.safe_load(repo.read_text())
    assert any(c["zk"] == "lzk" for c in data["contexts"]["available"])
    with open(cfg.config_path) as f:
        cfg_data = yaml.safe_load(f) or {}
    assert any(c["zk"] == "newzk" for c in cfg_data["contexts"]["available"])

    # delete from local first
    config_cmd.delete("local")
    with open(cfg.config_path) as f:
        cfg_data = yaml.safe_load(f) or {}
    assert not any(c["name"] == "local" for c in cfg_data["contexts"]["available"])
    data = yaml.safe_load(repo.read_text())
    assert any(c["name"] == "local" for c in data["contexts"]["available"])

    # delete remaining context from repo
    config_cmd.delete("local")
    data = yaml.safe_load(repo.read_text())
    assert not any(c["name"] == "local" for c in data["contexts"]["available"])


def test_list_contexts(monkeypatch, tmp_path, capsys):
    repo_content = """contexts:\n  available:\n    - name: repo\n      zk: rzk\n    - name: dup\n      zk: rdup\n"""
    settings_content = """context_repositories:\n  - {repo}\ncontexts:\n  available:\n    - name: dup\n      zk: ldup\n  current: {{name: dup}}\n"""
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content.format(repo=tmp_path / "repo.yaml"),
    )

    config_cmd.list_contexts()
    out = capsys.readouterr().out
    assert "repo" in out and str(repo) in out
    assert "dup" in out and str(cfg.config_path) in out
    assert "*" in out


def test_switch_outputs_location(monkeypatch, tmp_path, capsys):
    repo_content = """contexts:\n  available:\n    - name: r1\n      zk: rzk\n"""
    settings_content = """context_repositories:\n  - {repo}\ncontexts:\n  available:\n    - name: l1\n      zk: lzk\n  current: {{}}\n"""
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content.format(repo=tmp_path / "repo.yaml"),
    )

    monkeypatch.setattr(config_cmd, "_verify_zk_connection", lambda: True)

    config_cmd.switch("l1")
    out = capsys.readouterr().out
    assert "local configuration" in out

    config_cmd.switch("r1")
    out = capsys.readouterr().out
    assert f"repository {repo}" in out


def test_list_repos(monkeypatch, tmp_path, capsys):
    repo_content = """contexts:\n  available:\n    - name: r1\n      zk: rzk\n"""
    settings_content = """context_repositories:\n  - {repo}\ncontexts:\n  available: []\n  current: {{}}\n"""
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content.format(repo=tmp_path / "repo.yaml"),
    )

    config_cmd.list_repos()
    out = capsys.readouterr().out
    assert str(repo) in out
    assert "r1" in out


def test_open_repo(monkeypatch, tmp_path):
    repo_content = """contexts:\n  available: []\n"""
    settings_content = (
        """context_repositories:\n  - {repo}\ncontexts:\n  available: []\n  current: {{}}\n"""
    )
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content.format(repo=tmp_path / "repo.yaml"),
    )

    called = {}

    def fake_run(cmd):
        called["cmd"] = cmd

    monkeypatch.setattr(config_cmd.subprocess, "run", fake_run)

    config_cmd.open_repo(repo)
    assert called["cmd"][0] == "xdg-open"
    assert called["cmd"][1] == str(repo.parent)

