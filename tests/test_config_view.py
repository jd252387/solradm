import importlib
import json
from pathlib import Path


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


def test_view_configuration(monkeypatch, tmp_path, capsys):
    repo_content = """contexts:\n  available:\n    - name: repo\n      zk: rzk\n    - name: dup\n      zk: repo_dup\n"""
    settings_content = """context_repositories:\n  - name: shared\n    path: {repo}\ncontexts:\n  available:\n    - name: dup\n      zk: local_dup\n  current: {{name: dup}}\nauth:\n  user: alice\n  password: secret\nconfig_dir: {cfg}\n"""
    cfg_dir = tmp_path / "confdir"
    repo, cfg, config_cmd = _prepare(
        monkeypatch,
        tmp_path,
        repo_content,
        settings_content.format(repo=tmp_path / "repo.yaml", cfg=cfg_dir),
    )

    config_cmd.view_config()
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["config_dir"] == str(cfg_dir)
    assert data["auth"]["user"] == "alice"
    assert data["contexts"]["current"]["name"] == "dup"
    avail = {c["name"]: c["zk"] for c in data["contexts"]["available"]}
    assert avail["dup"] == "local_dup"
    merged = {c["name"]: c["zk"] for c in data["merged_contexts"]}
    assert merged["repo"] == "rzk"
    assert merged["dup"] == "local_dup"
    assert data["context_repositories"] == [
        {"name": "shared", "path": str(repo)}
    ]

