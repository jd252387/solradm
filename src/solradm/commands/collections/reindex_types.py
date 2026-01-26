from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SourceShardState:
    """State for a single source shard being reindexed."""
    name: str
    doc_count: int | None = None
    docs_processed: int = 0
    status: Literal["pending", "running", "done", "failed"] = "pending"
    error: str | None = None


@dataclass
class TargetShardState:
    """State for a target shard and all its source shards."""
    name: str
    source_shards: list[SourceShardState] = field(default_factory=list)
    current_source: str | None = None
    status: Literal["pending", "running", "done", "failed"] = "pending"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None

    @property
    def total_docs(self) -> int:
        """Total documents across all source shards."""
        return sum(s.doc_count or 0 for s in self.source_shards)

    @property
    def docs_processed(self) -> int:
        """Total documents processed across all source shards."""
        return sum(s.docs_processed for s in self.source_shards)


@dataclass
class ReindexConfig:
    """Configuration for a reindex operation."""
    target_collection: str
    source_collection: str
    handler: str = "/dataimport"
    rows: int = 2000
    sort: str = "first_timestamp asc, item_id asc"
    qt: str = "/dih"
    fl: str = "*,ignored_tmp1:_version_"
    timeout: int = 300
    fq: list[str] | None = None
