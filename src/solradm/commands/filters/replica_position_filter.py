import re
from dataclasses import field, dataclass
from typing import Optional, List

import typer

from solradm.api.models import Collection
from solradm.commands.filters.filter import Filter
from solradm.completion.collections import replica_positions


@dataclass
class ReplicaPositionFilter(Filter):
    """Filter replicas by their position within a shard."""
    replica_position: Optional[int] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--replica-position",
                help="Select only the replica at this 1-indexed position",
                autocompletion=replica_positions,
            )
        },
    )
    exclude_replica_position: Optional[int] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--exclude-replica-position",
                help="Exclude the replica at this 1-indexed position",
                autocompletion=replica_positions,
            )
        },
    )

    def init(self):
        # nothing required on init
        pass

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        filtered_collections = []
        for collection in cluster_state:
            new_shards = []
            for shard in collection.shards:
                sorted_replicas = sorted(
                    shard.replicas,
                    key=lambda r: int(re.findall(r"\d+", r.name)[0]),
                )
                new_replicas = []
                for idx, replica in enumerate(sorted_replicas, start=1):
                    match_include = (
                        (idx == self.replica_position or (
                                self.replica_position > idx == len(sorted_replicas)) or (
                                 self.replica_position < idx == 1)) if self.replica_position is not None else True
                    )
                    match_exclude = (
                        idx == self.exclude_replica_position
                        if self.exclude_replica_position is not None
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

    def describe(self) -> List[str]:
        descriptions: List[str] = []
        if self.replica_position is not None:
            descriptions.append(
                f"All replicas whose position is {self.replica_position} within their corresponding shard"
            )
        if self.exclude_replica_position is not None:
            descriptions.append(
                f"Exclude replicas whose position is {self.exclude_replica_position} within their corresponding shard"
            )
        return descriptions
