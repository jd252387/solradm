import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from solradm.commands.collections.reindex_engine import ReindexEngine
from solradm.commands.collections.reindex_types import ReindexConfig, SourceShardState


@pytest.fixture
def mock_leaders():
    """Mock leader replicas for target shards."""
    from solradm.api.models import Replica
    return {
        "shard1": Replica(
            name="core_node1",
            core="target_shard1_replica1",
            node_name="node1:8983_solr",
            type="NRT",
            state="active",
            leader=True,
            force_set_state=False,
            base_url="http://node1:8983/solr",
        ),
        "shard2": Replica(
            name="core_node2",
            core="target_shard2_replica1",
            node_name="node2:8983_solr",
            type="NRT",
            state="active",
            leader=True,
            force_set_state=False,
            base_url="http://node2:8983/solr",
        ),
    }


@pytest.fixture
def mock_shard_map():
    """Mock source-to-target shard mapping."""
    from solradm.api.models import Shard, Replica

    def make_shard(name: str) -> Shard:
        replica = Replica(
            name=f"{name}_replica",
            core=f"{name}_core",
            node_name="srcnode:8983_solr",
            type="NRT",
            state="active",
            leader=True,
            force_set_state=False,
            base_url="http://srcnode:8983/solr",
        )
        return Shard(name=name, range="80000000-ffffffff", replicas=[replica])

    return {
        "shard1": [make_shard("src_001"), make_shard("src_002")],
        "shard2": [make_shard("src_003")],
    }


@pytest.fixture
def config():
    return ReindexConfig(
        target_collection="target_coll",
        source_collection="source_coll",
    )


def test_engine_initialization(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)
    state = engine.get_state()

    assert len(state) == 2
    assert state[0].name == "shard1"
    assert len(state[0].source_shards) == 2
    assert state[1].name == "shard2"
    assert len(state[1].source_shards) == 1


def test_engine_get_summary(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)
    summary = engine.get_summary()

    assert summary["total"] == 2
    assert summary["pending"] == 2
    assert summary["running"] == 0
    assert summary["done"] == 0
    assert summary["failed"] == 0


def test_adaptive_poll_interval(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)

    # With 0 running, should be 1 second
    assert engine._get_poll_interval() == 1.0

    # Simulate running state
    engine._target_states[0].status = "running"
    assert engine._get_poll_interval() == 1.0


def test_engine_run_success(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)

    # Mock the async methods
    with patch.object(engine, '_get_shard_doc_count', new_callable=AsyncMock) as mock_count, \
         patch.object(engine, '_start_dataimport', new_callable=AsyncMock) as mock_start, \
         patch.object(engine, '_poll_dataimport_status', new_callable=AsyncMock) as mock_poll:

        mock_count.return_value = 1000
        mock_poll.return_value = ("done", 1000)

        asyncio.run(engine.run())

        summary = engine.get_summary()
        assert summary["done"] == 2
        assert summary["failed"] == 0
