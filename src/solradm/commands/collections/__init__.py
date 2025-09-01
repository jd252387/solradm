import asyncio
import re
from collections import Counter, defaultdict
from typing import List

import rich
import typer
from rich.prompt import Confirm
from rich.table import Table
from async_typer import AsyncTyper

from solradm.api.models import Collection
from solradm.api.state import get_nodes_by_role
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


@app.async_command()
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter)
async def populate(
        cluster_state: List[Collection],
        node: List[str] | None = typer.Option(None, "--node", help="Regex to select nodes"),
        exclude_node: List[str] | None = typer.Option(None, "--exclude-node", help="Regex to exclude nodes"),
):
    if len(cluster_state) != 1:
        rich.print("[error] ❌ More than one collection has been filtered, and this command requires a singular collection!")
        raise typer.Exit(1)

    collection = cluster_state[0]
    shards = collection.shards

    data_nodes = get_nodes_by_role("data").get("on", [])

    include_patterns = [re.compile(p) for p in node] if node else []
    exclude_patterns = [re.compile(p) for p in exclude_node] if exclude_node else []

    def match_node(n: str) -> bool:
        if include_patterns and not any(p.search(n) for p in include_patterns):
            return False
        if exclude_patterns and any(p.search(n) for p in exclude_patterns):
            return False
        return True

    selected_nodes = [n for n in data_nodes if match_node(n)]

    if not selected_nodes:
        rich.print("[error] ❌ No nodes match the given selectors")
        raise typer.Exit(1)

    node_to_shards = defaultdict(list)
    for shard in shards:
        existing_nodes = {r.node_name for r in shard.replicas}
        for n in selected_nodes:
            if n not in existing_nodes:
                node_to_shards[n].append(shard)

    if not node_to_shards:
        rich.print("[warning] ⚠️ No replicas need to be created on the selected nodes")
        raise typer.Exit(0)

    table = Table(title="Planned replicas to add")
    table.add_column("Node")
    table.add_column("Shards")
    for n, shards_list in node_to_shards.items():
        table.add_row(n, ", ".join(s.name for s in shards_list))
    rich.print(table)

    counts = [len(s) for s in node_to_shards.values()]
    avg = sum(counts) / len(counts)
    dist = Counter(counts)

    rich.print(f"Average replicas per node: {avg:.2f}")

    dist_table = Table(title="Replica distribution")
    dist_table.add_column("Replicas")
    dist_table.add_column("Nodes")
    for num, cnt in sorted(dist.items()):
        dist_table.add_row(str(num), str(cnt))
    rich.print(dist_table)

    if not Confirm.ask("Proceed with adding replicas?"):
        raise typer.Exit(0)

    tasks = []
    for n, shards_list in node_to_shards.items():
        for shard in shards_list:
            tasks.append(
                MetaTask(
                    [collection.name, shard.name, n],
                    asyncio.create_task(
                        send_request(
                            get_overseer_leader(),
                            "/admin/collections",
                            params={
                                "action": "ADDREPLICA",
                                "collection": collection.name,
                                "shard": shard.name,
                                "node": n,
                            },
                        )
                    ),
                )
            )

    metatasks = MultiMetaTask(["collection", "shard", "node"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))
