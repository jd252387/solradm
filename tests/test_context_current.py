import importlib
import io
from pathlib import Path

from rich.console import Console


def _prepare(monkeypatch, tmp_path: Path, settings_content: str):
    config_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    cfg_dir = config_home / "solradm"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "settings.yaml").write_text(settings_content)

    import solradm.config as cfg

    importlib.reload(cfg)
    from solradm.commands import config as config_cmd

    importlib.reload(config_cmd)
    return config_cmd


def test_print_current_context_formats_lines(monkeypatch, tmp_path):
    settings_content = """contexts:\n  available:\n    - name: demo\n      zk: zkhost\n      kubecontext: kc\n      namespace: ns\n  current: {name: demo}\n"""

    config_cmd = _prepare(monkeypatch, tmp_path, settings_content)

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)

    config_cmd.print_current_context(console=console)

    out = buffer.getvalue()
    assert "Current context" in out
    assert "Name:" in out and "demo" in out
    assert "ZooKeeper:" in out and "zkhost" in out
    assert "Kubecontext:" in out and "kc" in out
    assert "Namespace:" in out and "ns" in out
