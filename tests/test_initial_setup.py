import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


class DummyContext:
    def __init__(self, name, zk, kubecontext=None):
        self.name = name
        self.zk = zk
        self.kubecontext = kubecontext

    def as_dict(self):
        data = {"name": self.name, "zk": self.zk}
        if self.kubecontext is not None:
            data["kubecontext"] = self.kubecontext
        return data


def _run_setup(monkeypatch, tmp_path: Path, confirm_answers, prompt_answers, repo_content: str):
    repo = tmp_path / "repo.yaml"
    repo.write_text(repo_content)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    confirm_iter = iter(confirm_answers)
    prompt_iter = iter(prompt_answers)

    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **k: next(confirm_iter))
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *a, **k: next(prompt_iter))

    stub_ctx = ModuleType("solradm.config.interactive.setup_context")
    stub_ctx.setup = lambda name: DummyContext(name, "lzk")
    stub_auth = ModuleType("solradm.config.interactive.setup_solrauth")
    stub_auth.setup = lambda: SimpleNamespace(login="u", password="p")
    stub_cfg = ModuleType("solradm.config.interactive.setup_config_dir")
    stub_cfg.setup = lambda: tmp_path / "confdir"
    sys.modules.pop("solradm.config", None)
    sys.modules.pop("solradm.config.interactive", None)
    for name, mod in {
        "solradm.config.interactive.setup_context": stub_ctx,
        "solradm.config.interactive.setup_solrauth": stub_auth,
        "solradm.config.interactive.setup_config_dir": stub_cfg,
    }.items():
        sys.modules.pop(name, None)
        sys.modules[name] = mod

    monkeypatch.setattr("builtins.exit", lambda code=0: None)

    import solradm.config as cfg
    return cfg, repo


def test_initial_setup_repo_only(monkeypatch, tmp_path):
    repo_content = """contexts:\n  available:\n    - name: r1\n      zk: z1\n    - name: r2\n      zk: z2\n"""
    cfg, repo = _run_setup(
        monkeypatch,
        tmp_path,
        confirm_answers=[False, True],
        prompt_answers=[str(tmp_path / "repo.yaml")],
        repo_content=repo_content,
    )

    assert cfg.settings.contexts.current.name == "r1"
    names = [c["name"] for c in cfg.settings.contexts.available]
    assert names == ["r1", "r2"]
    assert cfg.settings.get("context_repositories") == [str(repo)]


def test_initial_setup_with_local_and_repo(monkeypatch, tmp_path):
    repo_content = """contexts:\n  available:\n    - name: r1\n      zk: z1\n"""
    cfg, repo = _run_setup(
        monkeypatch,
        tmp_path,
        confirm_answers=[True, True],
        prompt_answers=["local", str(tmp_path / "repo.yaml")],
        repo_content=repo_content,
    )

    current = cfg.settings.contexts.current.name
    names = {c["name"] for c in cfg.settings.contexts.available}
    assert current == "local"
    assert names == {"local", "r1"}
    assert cfg.settings.get("context_repositories") == [str(repo)]
