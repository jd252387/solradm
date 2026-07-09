import asyncio
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
from solradm.commands.collections.export_state import export_state


def _replica(name: str, core: str, node_name: str, state: str = "active") -> Replica:
    return Replica(
        name=name,
        core=core,
        node_name=node_name,
        type="NRT",
        state=state,
        leader=False,
        force_set_state=False,
        base_url=f"http://{node_name}:8983/solr",
    )


def _cluster_state() -> list[Collection]:
    shard1 = Shard(
        name="shard1",
        range="0-100",
        replicas=[
            _replica("core_node1", "coll_shard1_replica_n1", "solr01:8983_solr"),
            _replica("core_node2", "coll_shard1_replica_n2", "solr02:8983_solr", "down"),
        ],
    )
    shard2 = Shard(
        name="shard2",
        range="100-200",
        replicas=[
            _replica("core_node3", "coll_shard2_replica_n3", "solr01:8983_solr"),
        ],
    )
    return [
        Collection(
            name="mycoll",
            pullReplicas=0,
            configName="myconf",
            replicationFactor=1,
            router=Router(name="compositeId", field=None),
            nrtReplicas=1,
            tlogReplicas=0,
            shards=[shard1, shard2],
        )
    ]


def test_export_state_writes_requested_fields_in_order(tmp_path):
    out = tmp_path / "cores.txt"
    asyncio.run(
        export_state(
            cluster_state=_cluster_state(),
            file_name=out,
            output=["collection", "shard", "core", "node", "config", "replica_state"],
        )
    )

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "mycoll shard1 coll_shard1_replica_n1 solr01:8983_solr myconf active",
        "mycoll shard1 coll_shard1_replica_n2 solr02:8983_solr myconf down",
        "mycoll shard2 coll_shard2_replica_n3 solr01:8983_solr myconf active",
    ]


def test_export_state_single_field(tmp_path):
    out = tmp_path / "nodes.txt"
    asyncio.run(
        export_state(
            cluster_state=_cluster_state(),
            file_name=out,
            output=["node"],
        )
    )
    assert out.read_text(encoding="utf-8").splitlines() == [
        "solr01:8983_solr",
        "solr02:8983_solr",
        "solr01:8983_solr",
    ]


def test_export_state_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "dir" / "cores.txt"
    asyncio.run(
        export_state(
            cluster_state=_cluster_state(),
            file_name=out,
            output=["core"],
        )
    )
    assert out.exists()
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_export_state_invalid_field_raises(tmp_path):
    out = tmp_path / "cores.txt"
    with pytest.raises(typer.BadParameter):
        asyncio.run(
            export_state(
                cluster_state=_cluster_state(),
                file_name=out,
                output=["core", "bogus"],
            )
        )
    assert not out.exists()


def test_export_state_no_cores_writes_nothing(tmp_path):
    out = tmp_path / "cores.txt"
    empty_collection = Collection(
        name="empty",
        pullReplicas=0,
        configName="myconf",
        replicationFactor=1,
        router=Router(name="compositeId", field=None),
        nrtReplicas=1,
        tlogReplicas=0,
        shards=[Shard(name="shard1", range="0-100", replicas=[])],
    )
    asyncio.run(
        export_state(
            cluster_state=[empty_collection],
            file_name=out,
            output=["core"],
        )
    )
    assert not out.exists()
