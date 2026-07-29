import importlib
import sys
from pathlib import Path

import typer
from typer.testing import CliRunner


def _load_status(monkeypatch, tmp_path, collections):
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
    monkeypatch.setattr(filters_utils, "get_collections", lambda: collections)

    return importlib.import_module("solradm.commands.status")


def _collection(name: str):
    from solradm.api.models import Collection, Replica, Router, Shard

    return Collection(
        name=name,
        pullReplicas=0,
        configName="myconf",
        replicationFactor=1,
        router=Router(name="compositeId", field=None),
        nrtReplicas=1,
        tlogReplicas=0,
        shards=[
            Shard(
                name="shard1",
                range="0-100",
                replicas=[
                    Replica(
                        name="core_node1",
                        core=f"{name}_shard1_replica_n1",
                        node_name="solr01:8983_solr",
                        type="NRT",
                        state="active",
                        leader=False,
                        force_set_state=False,
                        base_url="http://solr01:8983/solr",
                    )
                ],
            )
        ],
    )


def _invoke(status_module, args):
    """Run through Typer so option defaults resolve the way the real CLI resolves them."""
    app = typer.Typer()
    app.command()(status_module.status)
    return CliRunner().invoke(app, args)


def test_status_handles_empty_cluster(monkeypatch, capsys, tmp_path):
    status_module = _load_status(monkeypatch, tmp_path, [])

    status_module.status(severity=None, show_max=20)

    out = capsys.readouterr().out

    assert "No collections found in the cluster." in out


def test_status_without_collection_filter_shows_every_collection(monkeypatch, tmp_path):
    status_module = _load_status(
        monkeypatch, tmp_path, [_collection("logs"), _collection("metrics")]
    )

    result = _invoke(status_module, [])

    assert result.exit_code == 0
    assert "logs" in result.output
    assert "metrics" in result.output


def test_status_collection_filter_narrows_by_regex(monkeypatch, tmp_path):
    status_module = _load_status(
        monkeypatch, tmp_path, [_collection("logs"), _collection("metrics")]
    )

    result = _invoke(status_module, ["-c", "^log"])

    assert result.exit_code == 0
    assert "logs" in result.output
    assert "metrics" not in result.output


def test_status_collection_filter_reports_no_match(monkeypatch, tmp_path):
    status_module = _load_status(monkeypatch, tmp_path, [_collection("logs")])

    result = _invoke(status_module, ["-c", "nothing-matches-this"])

    assert result.exit_code == 0
    assert "No collections matched the pattern" in result.output


def test_status_collection_filter_rejects_invalid_regex(monkeypatch, tmp_path):
    status_module = _load_status(monkeypatch, tmp_path, [_collection("logs")])

    result = _invoke(status_module, ["-c", "["])

    assert result.exit_code != 0
    assert "Invalid regex pattern" in result.output
