from dataclasses import field, dataclass
from typing import Optional, List

import typer

from solradm import completion
from solradm.api.models import Collection
from solradm.commands.filters.filter import Filter


@dataclass
class ReplicaTypeFilter(Filter):
    """Filter replicas by type (leader or follower)."""
    replica_type: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--replica-type",
                help="Replica type to include: 'leader' or 'follower'",
                autocompletion=completion.replica_types,
            )
        },
    )
    exclude_replica_type: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--exclude-replica-type",
                help="Replica type to exclude: 'leader' or 'follower'",
                autocompletion=completion.replica_types,
            )
        },
    )

    def init(self):
        valid = {"leader", "follower", None}
        if self.replica_type not in valid or self.exclude_replica_type not in valid:
            raise typer.BadParameter("Replica type must be 'leader' or 'follower'")

    def _is_type(self, replica, type_name: str) -> bool:
        if type_name == "leader":
            return replica.leader
        if type_name == "follower":
            return not replica.leader
        return False

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        filtered_collections = []
        for collection in cluster_state:
            new_shards = []
            for shard in collection.shards:
                new_replicas = []
                for replica in shard.replicas:
                    match_include = (
                        self._is_type(replica, self.replica_type) if self.replica_type else True
                    )
                    match_exclude = (
                        self._is_type(replica, self.exclude_replica_type)
                        if self.exclude_replica_type
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