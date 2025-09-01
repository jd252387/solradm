import asyncio
import re
from collections import Counter
from pathlib import Path
from typing import List

import rich
import typer
from rich.prompt import Confirm
from rich.table import Table
from async_typer import AsyncTyper

from solradm.api.models import Collection
from solradm.api.state import get_nodes_by_role
import solradm.api.utils as api_utils
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
from solradm.commands.zk.utils import create_or_update, get_relative_znode_path
from solradm.zk import get_client

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

    selected_nodes = sorted([n for n in data_nodes if match_node(n)])

    if not selected_nodes:
        rich.print("[error] ❌ No nodes match the given selectors")
        raise typer.Exit(1)

    shards_sorted = sorted(shards, key=lambda s: int(re.findall(r"\d+", s.name)[0]))

    num_nodes = len(selected_nodes)
    num_shards = len(shards_sorted)
    base = num_shards // num_nodes
    remainder = num_shards % num_nodes

    node_to_shards = {}
    idx = 0
    for i, n in enumerate(selected_nodes):
        count = base + (1 if i < remainder else 0)
        if count:
            node_to_shards[n] = shards_sorted[idx: idx + count]
            idx += count

    if not node_to_shards:
        rich.print("[warning] ⚠️ No replicas need to be created on the selected nodes")
        raise typer.Exit(0)

    table = Table(title="Planned replicas to add")
    table.add_column("Node")
    table.add_column("Shards")
    for n, shards_list in node_to_shards.items():
        table.add_row(n, ", ".join(s.name for s in shards_list))
    rich.print(table)

    counts = [len(node_to_shards.get(n, [])) for n in selected_nodes]
    avg = sum(counts) / len(selected_nodes)
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


@app.async_command()
@with_dry_run
async def create(
        name: str = typer.Argument(..., help="Name of the collection"),
        shards: int = typer.Option(..., "--shards", help="Number of shards"),
        conf: str = typer.Option(..., "--conf", help="Configuration name in ZooKeeper"),
        upload_conf: Path | None = typer.Option(
            None, "--upload-conf", exists=True, file_okay=False, dir_okay=True, resolve_path=True,
            help="Path to configuration directory to upload before creation"
        ),
        populate_after: bool = typer.Option(False, "--populate", help="Populate the collection after creation"),
        node: str | None = typer.Option(None, "--node", help="Regex to select nodes for populate"),
):
    if upload_conf:
        for f in Path(upload_conf).rglob("*"):
            if f.is_file():
                with open(f, "rb") as fh:
                    create_or_update(
                        get_client(),
                        get_relative_znode_path(f"/configs/{conf}", str(upload_conf), str(f)),
                        fh.read(),
                    )

    params = {
        "action": "CREATE",
        "name": name,
        "numShards": shards,
        "collection.configName": conf,
        "createNodeSet": "EMPTY",
    }
    await send_request(get_overseer_leader(), "/admin/collections", params=params)
    rich.print(f"[success]✅  Created collection {name}!")

    if populate_after:
        await populate(dry_run=api_utils.is_dry_run, collection_name_filter=name, node=[node] if node else None, exclude_node=None)
