import asyncio
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
from solradm.api.utils import get_replicas, send_request
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader

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
                help="Shard numbers to include (e.g. '1,3-5,2+3-7,+4-16')",
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
            seq_match = re.fullmatch(r"(?:(\d+)?\+(\d+)(?:-(\d+))?)", part)
            if seq_match:
                start = int(seq_match.group(1)) if seq_match.group(1) else 1
                step = int(seq_match.group(2))
                end = int(seq_match.group(3)) if seq_match.group(3) else None
                rules.append(("seq", start, step, end))
                continue
            range_match = re.fullmatch(r"(\d+)-(\d+)", part)
            if range_match:
                rules.append(("range", int(range_match.group(1)), int(range_match.group(2))))
                continue
            if part.isdigit():
                rules.append(("eq", int(part)))
                continue
            raise typer.BadParameter(f"Invalid shard specification '{part}'")
        return rules

    def _matches(self, rules, shard_num: int) -> bool:
        for rule in rules:
            kind = rule[0]
            if kind == "eq" and shard_num == rule[1]:
                return True
            if kind == "range" and rule[1] <= shard_num <= rule[2]:
                return True
            if kind == "seq":
                start, step, end = rule[1], rule[2], rule[3]
                if shard_num >= start and (shard_num - start) % step == 0:
                    if end is None or shard_num <= end:
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
                        idx == self.replica_position if self.replica_position is not None else True
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
            )
        },
    )

    def init(self):
        valid = {"active", "down", "recovering", "recovery_failed", None}
        if self.replica_state not in valid or self.exclude_replica_state not in valid:
            raise typer.BadParameter(
                "Replica state must be one of 'active', 'down', 'recovering', 'recovery_failed'"
            )

    def apply(self, cluster_state: List[Collection]) -> List[Collection]:
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
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def full_reload(
        cluster_state: List[Collection]
):
    replicas = get_replicas(cluster_state)
    tasks = [
        MetaTask(
            [replica.base_url, replica.core],
            asyncio.create_task(send_request(get_overseer_leader(), "/admin/cores",
                                             params={"action": "RELOAD", "core": replica.core})),
        )
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["host", "core"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))
