import asyncio
import json
from typing import List

import rich
import typer
from async_typer import AsyncTyper

from solradm.api.models import Collection, Replica
from solradm.api.state import get_nodes_by_role
from solradm.api.utils import get_replicas, send_request, get_cores_from_node, validate_num_replicas
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

@app.async_command(help="Reload cores for filtered replicas")
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def reload(
        cluster_state: List[Collection],
        coordinators: bool = typer.Option(True, help="Also reload coordinators")
):
    """Reload the specified cores and optionally coordinators."""

    replicas = get_replicas(cluster_state)
    if coordinators:
        coordinator_nodes = get_nodes_by_role("coordinator")["on"]
        for node in coordinator_nodes:
            cores = await get_cores_from_node(node)
            for core in cores:
                replicas.append(
                    Replica(name=core.cloud.replica, core=core.name, node_name=node, type=core.cloud.replicaType,
                            state=core.lastPublished, leader=True, force_set_state=False, base_url=node))

    validate_num_replicas(replicas)

    tasks = [
        MetaTask(
            [replica.base_url, replica.core],
            asyncio.create_task(send_request(replica.base_url, "/admin/cores",
                                             params={"action": "RELOAD", "core": replica.core})))
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["host", "core"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))


@app.async_command(help="Execute a query against a collection")
async def query(
        collection: str = typer.Argument(..., help="Collection to query"),
        q: str = typer.Argument(..., help="Lucene query string"),
        rows: int = typer.Option(10, help="Number of rows to return"),
        fl: str = typer.Option("*", help="Fields to return"),
        debug: bool = typer.Option(False, help="Include debug information"),
):
    """Query a collection and pretty-print the top results."""

    params = {"q": q, "rows": rows, "fl": fl}
    if debug:
        params["debug"] = "true"

    resp = await send_request(get_overseer_leader(), f"/{collection}/select", params=params)

    docs = resp.get("response", {}).get("docs", [])
    rich.print_json(data=json.dumps(docs))

    if debug and "debug" in resp:
        rich.print_json(data=json.dumps(resp["debug"]))
