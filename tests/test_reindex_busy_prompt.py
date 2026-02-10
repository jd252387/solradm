import importlib
import sys
from pathlib import Path

import asyncio

import pytest
import typer


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
        "solradm.commands.collections.reindex_ui",
        "solradm.commands.collections.reindex",
    ]:
        sys.modules.pop(module, None)

    reindex_ui = importlib.import_module("solradm.commands.collections.reindex_ui")
    reindex = importlib.import_module("solradm.commands.collections.reindex")
    models = importlib.import_module("solradm.api.models")
    return reindex, reindex_ui, models


def _replica(models, name: str):
    return models.Replica(
        name=name,
        core=f"{name}_core",
        node_name="node1",
        type="NRT",
        state="active",
        leader=True,
        force_set_state=False,
        base_url="http://localhost:8983/solr",
    )


def test_handle_busy_shards_prompts_with_count_only(monkeypatch, capsys, tmp_path):
    reindex, _reindex_ui, models = _load_modules(monkeypatch, tmp_path)
    leaders = {"shard1": _replica(models, "r1"), "shard2": _replica(models, "r2")}
    busy = [("shard1", leaders["shard1"])]

    monkeypatch.setattr(reindex.typer, "confirm", lambda *_args, **_kwargs: False)

    with pytest.raises(typer.Exit):
        asyncio.run(reindex._handle_busy_shards(busy, leaders, "/target/dataimport"))

    out = capsys.readouterr().out
    assert "running on 1 shard(s)" in out
    assert "shard1" not in out


def test_handle_busy_shards_opens_ui_when_confirmed(monkeypatch, tmp_path):
    reindex, _reindex_ui, models = _load_modules(monkeypatch, tmp_path)
    leaders = {"shard1": _replica(models, "r1")}
    busy = [("shard1", leaders["shard1"])]
    called = {}

    class FakeBusyDataimportApp:
        def __init__(self, arg_leaders, arg_path):
            called["leaders"] = arg_leaders
            called["path"] = arg_path

        async def run_async(self):
            called["ran"] = True

    monkeypatch.setattr(reindex.typer, "confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reindex, "BusyDataimportApp", FakeBusyDataimportApp)

    with pytest.raises(typer.Exit):
        asyncio.run(reindex._handle_busy_shards(busy, leaders, "/target/dataimport"))

    assert called["leaders"] == leaders
    assert called["path"] == "/target/dataimport"
    assert called["ran"] is True


def test_parse_busy_status_marks_not_running(monkeypatch, tmp_path):
    _reindex, reindex_ui, _models = _load_modules(monkeypatch, tmp_path)

    status, progress = reindex_ui._parse_busy_status({"status": "idle", "statusMessages": {}})
    assert status == "not_running"
    assert progress == ""
