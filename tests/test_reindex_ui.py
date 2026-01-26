import pytest
from textual.widgets import Static
from unittest.mock import MagicMock, AsyncMock

from solradm.commands.collections.reindex_ui import SummaryBar
from solradm.commands.collections.reindex_types import SourceShardState, TargetShardState


def test_summary_bar_format_docs():
    bar = SummaryBar()
    assert bar._format_docs(0) == "0"
    assert bar._format_docs(999) == "999"
    assert bar._format_docs(1000) == "1.0K"
    assert bar._format_docs(1500) == "1.5K"
    assert bar._format_docs(1000000) == "1.0M"
    assert bar._format_docs(1234567) == "1.2M"


def test_summary_bar_format_time():
    bar = SummaryBar()
    assert bar._format_time(0) == "0s"
    assert bar._format_time(45) == "45s"
    assert bar._format_time(60) == "1m 0s"
    assert bar._format_time(90) == "1m 30s"
    assert bar._format_time(3661) == "1h 1m"


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.get_state.return_value = [
        TargetShardState(
            name="shard_001",
            source_shards=[SourceShardState(name="src_001", doc_count=1000, docs_processed=500, status="running")],
            status="running",
            current_source="src_001",
        ),
        TargetShardState(
            name="shard_002",
            source_shards=[SourceShardState(name="src_002", doc_count=2000, docs_processed=2000, status="done")],
            status="done",
        ),
    ]
    engine.get_summary.return_value = {
        "total": 2,
        "running": 1,
        "done": 1,
        "failed": 0,
        "pending": 0,
        "docs_processed": 2500,
        "total_docs": 3000,
    }
    engine.run = AsyncMock()
    return engine


def test_reindex_app_sort_key():
    from solradm.commands.collections.reindex_ui import ReindexApp

    # Status priority: running=0, pending=1, failed=2, done=3
    running = TargetShardState(name="b_shard", source_shards=[], status="running")
    pending = TargetShardState(name="a_shard", source_shards=[], status="pending")
    failed = TargetShardState(name="c_shard", source_shards=[], status="failed")
    done = TargetShardState(name="d_shard", source_shards=[], status="done")

    states = [done, pending, failed, running]
    sorted_states = sorted(states, key=ReindexApp._sort_key)

    assert sorted_states[0].status == "running"
    assert sorted_states[1].status == "pending"
    assert sorted_states[2].status == "failed"
    assert sorted_states[3].status == "done"
