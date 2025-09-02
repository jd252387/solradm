from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING

import typer

from solradm import completion
from solradm.commands.filters.filter import Filter

if TYPE_CHECKING:  # pragma: no cover
    from solradm.api.models import Collection


@dataclass
class ReplicaStateFilter(Filter):
    """Filter replicas by their state."""
    replica_state: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--replica-state",
                help="Replica state to include: 'active', 'down', 'recovering', 'recovery_failed'",
                autocompletion=completion.replica_states,
            )
        },
    )
    exclude_replica_state: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--exclude-replica-state",
                help="Replica state to exclude: 'active', 'down', 'recovering', 'recovery_failed'",
                autocompletion=completion.replica_states,
            )
        },
    )

    def init(self):
        valid = {"active", "down", "recovering", "recovery_failed", None}
        if self.replica_state not in valid or self.exclude_replica_state not in valid:
            raise typer.BadParameter(
                "Replica state must be one of 'active', 'down', 'recovering', 'recovery_failed'",
            )

    def apply(self, cluster_state: List["Collection"]) -> List["Collection"]:
        filtered_collections = []
        for collection in cluster_state:
            new_shards = []
            for shard in collection.shards:
                new_replicas = []
                for replica in shard.replicas:
                    match_include = (
                        replica.state == self.replica_state if self.replica_state else True
                    )
                    match_exclude = (
                        replica.state == self.exclude_replica_state
                        if self.exclude_replica_state
                        else False
                    )
                    if match_include and not match_exclude:
                        new_replicas.append(replica)
                if new_replicas:
                    shard.replicas = new_replicas
                    new_shards.append(shard)
            if new_shards:
                collection.shards = new_shards
                filtered_collections.append(collection)
        return filtered_collections
