import functools
import inspect
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

import typer
from async_typer import AsyncTyper
from rich.prompt import Confirm

from solradm.api.models import Collection
from solradm.api.state import get_collections

app = AsyncTyper()


class Filter(ABC):
    @abstractmethod
    def init(self):
        pass


@dataclass
class CollectionNameFilter(Filter):
    """Filter collections by name using regex."""
    collection_name_filter: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(None, "--collection", help="Regex pattern to filter collections by name")
        }
    )

    def init(self):
        if self.collection_name_filter is None:
            if not Confirm.ask(
                    "No collection filter was specified, so this command will run across all collections, adhering to any other filters you have placed.\nAre you sure you want to continue?"):
                raise typer.Exit(0)

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        try:
            pattern = re.compile(self.collection_name_filter)
            return [
                c for c in cluster_state
                if pattern.search(c.name)
            ]
        except re.error as e:
            raise typer.BadParameter(f"Invalid regex pattern '{self.collection_name_filter}': {e}")


@dataclass
class ShardFilter(Filter):
    """Filter shards by shard number specification."""
    shards: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--shards",
                help="Shard numbers to include (e.g. '1,3-5,2+3')",
            )
        },
    )
    exclude_shards: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None,
                "--exclude-shards",
                help="Shard numbers to exclude",
            )
        },
    )

    def init(self):
        # nothing required on init
        pass

    def _parse_spec(self, spec: str):
        rules = []
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "+" in part:
                start, step = part.split("+", 1)
                rules.append(("seq", int(start), int(step)))
            elif "-" in part:
                start, end = part.split("-", 1)
                rules.append(("range", int(start), int(end)))
            else:
                rules.append(("eq", int(part)))
        return rules

    def _matches(self, rules, shard_num: int) -> bool:
        for rule in rules:
            kind = rule[0]
            if kind == "eq" and shard_num == rule[1]:
                return True
            if kind == "range" and rule[1] <= shard_num <= rule[2]:
                return True
            if kind == "seq":
                start, step = rule[1], rule[2]
                if shard_num >= start and (shard_num - start) % step == 0:
                    return True
        return False

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
        include_rules = self._parse_spec(self.shards) if self.shards else []
        exclude_rules = self._parse_spec(self.exclude_shards) if self.exclude_shards else []

        filtered_collections = []
        for collection in cluster_state:
            new_shards = []
            for shard in collection.shards:
                match_include = (
                    self._matches(include_rules, int(re.findall(r"\d+", shard.name)[0]))
                    if include_rules
                    else True
                )
                match_exclude = (
                    self._matches(exclude_rules, int(re.findall(r"\d+", shard.name)[0]))
                    if exclude_rules
                    else False
                )
                if match_include and not match_exclude:
                    new_shards.append(shard)
            if new_shards:
                collection.shards = new_shards
                filtered_collections.append(collection)
        return filtered_collections


@dataclass
class ReplicaTypeFilter(Filter):
    """Filter replicas by type (leader or follower)."""
    replica_type: Optional[str] = field(
        default=None,
        metadata={
            "typer_option": typer.Option(
                None, "--replica-type", help="Replica type to include: 'leader' or 'follower'"
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


# @dataclass
# class ShardCountFilter:
#     """Filter collections by shard count range."""
#     min_shards: Optional[int] = field(
#         default=None,
#         metadata={
#             "typer_option": "--min-shards",
#             "help": "Minimum number of shards"
#         }
#     )
#     max_shards: Optional[int] = field(
#         default=None,
#         metadata={
#             "typer_option": "--max-shards",
#             "help": "Maximum number of shards"
#         }
#     )

#     def apply(self, cluster_state: ClusterState) -> ClusterState:
#         filtered_collections = cluster_state.collections

#         if self.min_shards is not None:
#             filtered_collections = [
#                 c for c in filtered_collections 
#                 if len(c.shards) >= self.min_shards
#             ]

#         if self.max_shards is not None:
#             filtered_collections = [
#                 c for c in filtered_collections 
#                 if len(c.shards) <= self.max_shards
#             ]

#         return ClusterState(collections=filtered_collections)


# @dataclass
# class ReplicationFactorFilter:
#     """Filter collections by replication factor range."""
#     min_replication: Optional[int] = field(
#         default=None,
#         metadata={
#             "typer_option": "--min-replication",
#             "help": "Minimum replication factor"
#         }
#     )
#     max_replication: Optional[int] = field(
#         default=None,
#         metadata={
#             "typer_option": "--max-replication",
#             "help": "Maximum replication factor"
#         }
#     )

#     def apply(self, cluster_state: ClusterState) -> ClusterState:
#         filtered_collections = cluster_state.collections

#         if self.min_replication is not None:
#             filtered_collections = [
#                 c for c in filtered_collections 
#                 if c.replicationFactor >= self.min_replication
#             ]

#         if self.max_replication is not None:
#             filtered_collections = [
#                 c for c in filtered_collections 
#                 if c.replicationFactor <= self.max_replication
#             ]

#         return ClusterState(collections=filtered_collections)


# @dataclass
# class CollectionTypeFilter:
#     """Filter collections by replica types."""
#     has_nrt: Optional[bool] = field(
#         default=None,
#         metadata={
#             "typer_option": "--has-nrt",
#             "help": "Filter collections that have NRT replicas"
#         }
#     )
#     has_tlog: Optional[bool] = field(
#         default=None,
#         metadata={
#             "typer_option": "--has-tlog",
#             "help": "Filter collections that have TLog replicas"
#         }
#     )
#     has_pull: Optional[bool] = field(
#         default=None,
#         metadata={
#             "typer_option": typer.Option(None, "--has-pull", help="Filter collections that have Pull replicas")
#         }
#     )

#     def apply(self, cluster_state: ClusterState) -> ClusterState:
#         filtered_collections = cluster_state.collections

#         if self.has_nrt is not None:
#             filtered_collections = [
#                 c for c in filtered_collections 
#                 if (c.nrtReplicas > 0) == self.has_nrt
#             ]

#         if self.has_tlog is not None:
#             filtered_collections = [
#                 c for c in filtered_collections 
#                 if (c.tlogReplicas > 0) == self.has_tlog
#             ]

#         if self.has_pull is not None:
#             filtered_collections = [
#                 c for c in filtered_collections 
#                 if (c.pullReplicas > 0) == self.has_pull
#             ]

#         return ClusterState(collections=filtered_collections)


def with_cluster_state(*filter_classes):
    """
    Decorator that automatically fetches ClusterState and optionally applies filters.
    
    Args:
        *filter_classes: Optional filter classes to apply to the cluster state
    """

    def decorator(func):
        orig_sig = inspect.signature(func)
        new_params = [p for p in list(orig_sig.parameters.values()) if p.name != "cluster_state"]

        if filter_classes:
            orig_sig = inspect.signature(func)

            for filter_class in filter_classes:
                for field_name, field_info in filter_class.__dataclass_fields__.items():
                    typer_option = field_info.metadata.get("typer_option")

                    new_params.append(inspect.Parameter(
                        field_name,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        default=typer_option,
                        annotation=field_info.type | None
                    ))

        new_sig = orig_sig.replace(parameters=new_params)

        func.__signature__ = new_sig

        @functools.wraps(func)
        def wrapper(*args, **kwargs):

            filter_instances = []
            for filter_class in filter_classes:
                filter_params = {}
                for field_name in filter_class.__dataclass_fields__:
                    if field_name in kwargs:
                        filter_params[field_name] = kwargs.pop(field_name)

                filter_instance = filter_class(**filter_params)
                filter_instance.init()

                if any(filter_params.values()):
                    filter_instances.append(filter_instance)
            try:
                cluster_state = get_collections()
            except Exception as e:
                raise typer.BadParameter(f"Failed to fetch cluster state: {e}")

            for filter_instance in filter_instances:
                cluster_state = filter_instance.apply(cluster_state)

            return func(cluster_state=cluster_state, *args, **kwargs)

        return wrapper

    return decorator


@app.async_command()
@with_cluster_state(CollectionNameFilter)
async def full_reload(
        cluster_state: List[Collection]
):
    tasks = [
        MetaTask(
            [descriptor.base_url, descriptor.core_name],
            asyncio.create_task(reload_core(descriptor)),
        )
        for descriptor in pending
    ]
    table = MultiTaskTable(MultiMetaTask(["host", "core"], tasks), refresh_every=0.25)
    await asyncio.gather(*[task.task for task in tasks])
    table.stop()
