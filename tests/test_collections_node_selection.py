import os
import tempfile
from pathlib import Path

_test_config_home = Path(tempfile.gettempdir()) / "solradm-test-config"
(_test_config_home / "solradm").mkdir(parents=True, exist_ok=True)
settings_file = _test_config_home / "solradm" / "settings.yaml"
if not settings_file.exists():
    settings_file.write_text("contexts:\n  available: []\n  current: {name: default}\n")

os.environ.setdefault("XDG_CONFIG_HOME", str(_test_config_home))

import pytest
import typer

from solradm.api.models import Collection, Replica, Router, Shard
from solradm.commands.collections.lifecycle import _sort_nodes
from solradm.commands.filters.node_name_filter import NodeNameFilter


def _replica(node_name: str) -> Replica:
    return Replica(
        name=f"r-{node_name}",
        core="core",
        node_name=node_name,
        type="NRT",
        state="active",
        leader=False,
        force_set_state=False,
        base_url=f"http://{node_name}:8983/solr",
    )


def _collection(replicas: list[Replica]) -> Collection:
    shard = Shard(name="shard1", range="0-100", replicas=replicas)
    return Collection(
        name="coll",
        pullReplicas=0,
        configName="conf",
        replicationFactor=1,
        router=Router(name="implicit", field=None),
        nrtReplicas=1,
        tlogReplicas=0,
        shards=[shard],
    )


def test_node_filter_returns_all_when_no_filters():
    replicas = [_replica("solr02"), _replica("solr01"), _replica("solr01")]
    cluster_state = [_collection(replicas)]

    node_filter = NodeNameFilter()
    node_filter.init()

    filtered_state = node_filter.apply(cluster_state)

    assert len(filtered_state) == 1
    assert sorted({r.node_name for r in filtered_state[0].shards[0].replicas}) == [
        "solr01",
        "solr02",
    ]


def test_node_filter_honours_include_and_exclude_patterns():
    replicas = [_replica("solr01"), _replica("solr02"), _replica("solr03")]
    cluster_state = [_collection(replicas)]

    node_filter = NodeNameFilter(node=["solr0[12]"], exclude_node=["solr02"])
    node_filter.init()

    filtered_state = node_filter.apply(cluster_state)

    assert len(filtered_state[0].shards[0].replicas) == 1
    assert filtered_state[0].shards[0].replicas[0].node_name == "solr01"


def test_select_nodes_invalid_include_pattern_raises_bad_parameter():
    with pytest.raises(typer.BadParameter) as exc:
        node_filter = NodeNameFilter(node=["["])
        node_filter.init()

    assert "--node" in str(exc.value)


def test_select_nodes_invalid_exclude_pattern_raises_bad_parameter():
    with pytest.raises(typer.BadParameter) as exc:
        node_filter = NodeNameFilter(exclude_node=["["])
        node_filter.init()

    assert "--exclude-node" in str(exc.value)


def test_sort_nodes_alphabetical():
    nodes = ["solr02", "solr01", "solr03"]

    assert _sort_nodes(nodes, "alphabetical") == ["solr01", "solr02", "solr03"]


def test_sort_nodes_numerical_with_tiebreaker():
    nodes = ["solr10b", "solr2", "solr10a"]

    assert _sort_nodes(nodes, "numerical") == ["solr2", "solr10a", "solr10b"]


def test_sort_nodes_numerical_missing_digits_raises():
    with pytest.raises(typer.BadParameter) as exc:
        _sort_nodes(["solrA", "solr1"], "numerical")

    assert "lacks digits" in str(exc.value)
