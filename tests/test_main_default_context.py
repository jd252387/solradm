import importlib
import sys
from pathlib import Path


def test_run_without_arguments_shows_current_context(monkeypatch, tmp_path: Path):
    config_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    cfg_dir = config_home / "solradm"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "settings.yaml").write_text(
        """contexts:\n  available:\n    - name: default\n      zk: zkhost\n  current: {name: default}\n"""
    )

    for module in ["solradm.config", "solradm.commands.config", "solradm.main"]:
        sys.modules.pop(module, None)

    main = importlib.import_module("solradm.main")
    config_cmd = importlib.import_module("solradm.commands.config")

    called = {}

    def fake_print_current_context():
        called["called"] = True

    monkeypatch.setattr(config_cmd, "print_current_context", fake_print_current_context)
    monkeypatch.setattr(main, "notify_if_outdated", lambda: None)
    monkeypatch.setattr(main, "get_initialized_session", lambda: None)
    monkeypatch.setattr(sys, "argv", ["sa"])

    # Ensure main uses the patched config module reference
    monkeypatch.setattr(main, "config", config_cmd)

    main.run()

    assert called.get("called") is True
