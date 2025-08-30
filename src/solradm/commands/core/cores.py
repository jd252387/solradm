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
                filter_instance.apply(cluster_state)

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
