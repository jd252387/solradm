import importlib
import sys
from pathlib import Path


def _prepare_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from platformdirs import user_config_dir

    config_path = Path(user_config_dir("solradm", "eclipse")) / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "contexts: {available: [{name: default, zk: localhost:2181}], current: {name: default}}\n"
    )


def _load_modules(monkeypatch, tmp_path):
    _prepare_config(monkeypatch, tmp_path)
    for module in [
        "solradm.config",
        "solradm.commands.collections.reindex",
        "solradm.api.models",
    ]:
        sys.modules.pop(module, None)

    reindex = importlib.import_module("solradm.commands.collections.reindex")
    models = importlib.import_module("solradm.api.models")
    return reindex, models


def _shard(models, name: str):
    return models.Shard(name=name, range="0-0", replicas=[])


def test_map_source_to_targets_assigns_contiguous_ranges(monkeypatch, tmp_path):
    reindex, models = _load_modules(monkeypatch, tmp_path)
    source = [_shard(models, f"shard{i}") for i in range(1, 9)]
    target = [_shard(models, "shard1"), _shard(models, "shard2")]

    mapped = reindex._map_source_to_targets(source, target)

    assert [s.name for s in mapped["shard1"]] == ["shard1", "shard2", "shard3", "shard4"]
    assert [s.name for s in mapped["shard2"]] == ["shard5", "shard6", "shard7", "shard8"]


def test_map_source_to_targets_balances_when_not_even(monkeypatch, tmp_path):
    reindex, models = _load_modules(monkeypatch, tmp_path)
    source = [_shard(models, f"shard{i}") for i in range(1, 6)]
    target = [_shard(models, f"shard{i}") for i in range(1, 4)]

    mapped = reindex._map_source_to_targets(source, target)

    assert [s.name for s in mapped["shard1"]] == ["shard1"]
    assert [s.name for s in mapped["shard2"]] == ["shard2", "shard3"]
    assert [s.name for s in mapped["shard3"]] == ["shard4", "shard5"]
