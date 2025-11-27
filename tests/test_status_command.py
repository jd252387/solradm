import importlib
import sys
from pathlib import Path


def test_status_handles_empty_cluster(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from platformdirs import user_config_dir

    config_path = Path(user_config_dir("solradm", "eclipse")) / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("contexts: {available: [], current: {name: default}}\n")

    for module in [
        "solradm.config",
        "solradm.commands.filters.utils",
        "solradm.commands.status",
    ]:
        sys.modules.pop(module, None)

    filters_utils = importlib.import_module("solradm.commands.filters.utils")
    monkeypatch.setattr(filters_utils, "get_collections", lambda: [])

    status_module = importlib.import_module("solradm.commands.status")

    status_module.status(severity=None, show_max=20)

    out = capsys.readouterr().out

    assert "No collections found in the cluster." in out
