from solradm.commands.collections.reindex_types import (
    SourceShardState,
    TargetShardState,
    ReindexConfig,
)


def test_source_shard_state_defaults():
    state = SourceShardState(name="shard_001")
    assert state.name == "shard_001"
    assert state.status == "pending"
    assert state.doc_count is None
    assert state.docs_processed == 0
    assert state.error is None


def test_target_shard_state_defaults():
    source = SourceShardState(name="src_001")
    state = TargetShardState(name="tgt_001", source_shards=[source])
    assert state.name == "tgt_001"
    assert state.status == "pending"
    assert state.current_source is None
    assert len(state.source_shards) == 1


def test_target_shard_state_progress_calculation():
    sources = [
        SourceShardState(name="src_001", doc_count=100, docs_processed=100, status="done"),
        SourceShardState(name="src_002", doc_count=200, docs_processed=50, status="running"),
        SourceShardState(name="src_003", doc_count=300, status="pending"),
    ]
    state = TargetShardState(name="tgt_001", source_shards=sources)
    # Total: 600 docs, processed: 150
    assert state.total_docs == 600
    assert state.docs_processed == 150


def test_reindex_config():
    config = ReindexConfig(
        target_collection="target_coll",
        source_collection="source_coll",
        handler="/dataimport",
        rows=2000,
    )
    assert config.target_collection == "target_coll"
    assert config.handler == "/dataimport"
