from dataclasses import dataclass, field
from typing import Optional, List

import typer

from solradm.api.models import Collection
from solradm.commands.filters.filter import Filter
from solradm.completion.static import REPLICA_STATES, replica_states

def _quote(states: List[str]) -> str:
    return ", ".join(f"'{state}'" for state in states)


_STATES_HELP = _quote(REPLICA_STATES)


@dataclass
class ReplicaStateFilter(Filter):
    """Filter replicas by their state."""
    replica_state: Optional[List[str]] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--replica-state",
                help=f"Replica state to include, repeat for several: {_STATES_HELP}",
                autocompletion=replica_states,
            )
        },
    )
    exclude_replica_state: Optional[List[str]] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--exclude-replica-state",
                help=f"Replica state to exclude, repeat for several: {_STATES_HELP}",
                autocompletion=replica_states,
            )
        },
    )

    def init(self):
        given = set(self.replica_state or []) | set(self.exclude_replica_state or [])
        invalid = sorted(given - set(REPLICA_STATES))
        if invalid:
            raise typer.BadParameter(
                f"Replica state must be one of {_STATES_HELP}, got {', '.join(invalid)}"
            )

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        include = set(self.replica_state or [])
        exclude = set(self.exclude_replica_state or [])
        filtered_collections = []
        for collection in cluster_state:
            new_shards = []
            for shard in collection.shards:
                new_replicas = []
                for replica in shard.replicas:
                    match_include = replica.state in include if include else True
                    match_exclude = replica.state in exclude
                    if match_include and not match_exclude:
                        new_replicas.append(replica)
                if new_replicas:
                    shard.replicas = new_replicas
                    new_shards.append(shard)
            if new_shards:
                collection.shards = new_shards
                filtered_collections.append(collection)
        return filtered_collections

    def describe(self) -> List[str]:
        descriptions: List[str] = []
        if self.replica_state:
            descriptions.append(
                f"Include replicas whose state is one of {_quote(self.replica_state)}"
            )
        if self.exclude_replica_state:
            descriptions.append(
                f"Exclude replicas whose state is one of {_quote(self.exclude_replica_state)}"
            )
        return descriptions
