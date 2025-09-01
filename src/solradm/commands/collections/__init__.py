import asyncio
from typing import Annotated, List, Collection

import typer
from async_typer import AsyncTyper

from solradm.api.utils import validate_num_replicas, get_replicas, send_request
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.replica_position_filter import ReplicaPositionFilter
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.replica_type_filter import ReplicaTypeFilter
from solradm.commands.filters.shard_filter import ShardFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader

app = AsyncTyper()

@app.async_command()
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def depopulate(
        cluster_state: List[Collection]
):
    replicas = validate_num_replicas(get_replicas(cluster_state))
    tasks = [
        MetaTask(
            [replica.shard.collection.name, replica.shard.name, replica.name],
            asyncio.create_task(send_request(get_overseer_leader(), "/admin/collections",
                                             params={"action": "DELETEREPLICA",
                                                     "collection": replica.shard.collection.name,
                                                     "shard": replica.shard.name, "replica": replica.name})),
        )
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["collection", "shard", "replica"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))
