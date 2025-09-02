import asyncio
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import List

import rich
import typer
from async_typer import AsyncTyper
from rich.prompt import Confirm
from rich.table import Table

import solradm.api.utils as api_utils
from solradm import completion
from solradm.api.models import Collection, Replica
from solradm.api.state import get_nodes_by_role, get_collections
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

@app.async_command(help="Remove replicas for filtered collections")
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def depopulate(
        cluster_state: List[Collection]
):
    """Remove replicas from the selected collections."""

    replicas = get_replicas(cluster_state)

    table = Table(title="Cluster State", header_style="bold magenta")
    table.add_column("Collection", style="cyan")
    table.add_column("Active Shards", justify="right", style="green")
    table.add_column("Active Replicas", justify="right", style="green")

    total_active_shards = 0
    total_active_replicas = 0
    for coll in cluster_state:
        active_shards = sum(
            1 for shard in coll.shards if any(r.state == "active" for r in shard.replicas)
        )
        active_replicas = sum(
            1 for shard in coll.shards for r in shard.replicas if r.state == "active"
        )
        total_active_shards += active_shards
        total_active_replicas += active_replicas
        table.add_row(coll.name, str(active_shards), str(active_replicas))

    table.add_row("[bold]TOTAL[/bold]", str(total_active_shards), str(total_active_replicas), style="bold")
    rich.print(table)

    if not Confirm.ask("Proceed with removing replicas?"):
        raise typer.Exit(0)

    replicas = validate_num_replicas(replicas)
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


@app.async_command(help="Add replicas to a collection across selected nodes")
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter)
async def populate(
        cluster_state: List[Collection],
        node: List[str] | None = typer.Option(
            None,
            "--node",
            help="Regex to select nodes",
            autocompletion=completion.node_names,
        ),
        exclude_node: List[str] | None = typer.Option(
            None,
            "--exclude-node",
            help="Regex to exclude nodes",
            autocompletion=completion.node_names,
        ),
):
    """Populate a single collection with replicas across nodes."""

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


@app.async_command(help="Create a new collection")
@with_dry_run
async def create(
        name: str = typer.Argument(..., help="Name of the collection"),
        shards: int = typer.Option(..., "--shards", help="Number of shards"),
        conf: str = typer.Option(
            None,
            "--conf",
            help="Configuration name in ZooKeeper",
            autocompletion=completion.config_names,
        ),
        upload_conf: Path | None = typer.Option(
            None,
            "--upload-conf",
            exists=False,
            resolve_path=False,
            help="Path or configset name to upload before creation",
        ),
        populate_after: bool = typer.Option(False, "--populate", help="Populate the collection after creation"),
        node: str | None = typer.Option(
            None,
            "--node",
            help="Regex to select nodes for populate",
            autocompletion=completion.node_names,
        ),
):
    """Create a collection in Solr."""

    if upload_conf:
        if conf:
            raise typer.BadParameter("You can't specify both --conf and --upload-conf!")
        from solradm.commands.zk.editor import upload
        upload(paths=[upload_conf], znode_path="/configs", only_used=False, reload=False, exclude=None, skip_checks=True)
        conf = os.path.basename(os.path.normpath(upload_conf))
    else:
        if not conf:
            raise typer.BadParameter("Either --conf or --upload-conf must be specified!")

    params = {
        "action": "CREATE",
        "name": name,
        "numShards": shards,
        "collection.configName": conf,
        "createNodeSet": "EMPTY",
    }
    await send_request(get_overseer_leader(), "/admin/collections", params=params)
    rich.print(f"[success] ✅ Created collection {name}!")

    if populate_after:
        await populate(dry_run=api_utils.is_dry_run, collection_name_filter=f"^{name}$", node=[node] if node else None, exclude_node=None)


@app.async_command(help="Delete collections matching a pattern")
@with_dry_run
async def delete(
        pattern: str = typer.Argument(..., help="Regex pattern for collection names"),
):
    """Delete collections and their replicas."""

    fil = CollectionNameFilter(collection_name_filter=pattern)
    cluster_state = fil.apply(get_collections())
    names = [c.name for c in cluster_state]
    if not names:
        rich.print("[error] ❌ No collections match the given pattern")
        raise typer.Exit(1)

    await depopulate(collection_name_filter=pattern, dry_run=api_utils.is_dry_run)
    if api_utils.is_dry_run:
        return

    for name in names:
        await send_request(
            get_overseer_leader(),
            "/admin/collections",
            params={"action": "DELETE", "name": name},
        )
        rich.print(f"[success]✅  Deleted collection {name}!")

@app.async_command(help="Reload cores for filtered replicas")
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def reload(
        cluster_state: List[Collection],
        coordinators: bool = typer.Option(None, help="If unset, reloads both data and coordinator nodes. If set to true, only reload coordinators. If set to false, only reload data nodes.")
):
    """Reload the specified cores and optionally coordinators."""
    replicas = []
    if coordinators is None or not coordinators:
        replicas.extend(get_replicas(cluster_state))
    if coordinators is None or coordinators:
        coordinator_nodes = get_nodes_by_role("coordinator")["on"]
        for node in coordinator_nodes:
            cores = await api_utils.get_cores_from_node(node)
            for core in cores:
                replicas.append(
                    Replica(name=core.name, core=core.name, node_name=node, type=core.cloud.replicaType,
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
        collection: str = typer.Argument(..., help="Collection to query", autocompletion=completion.collection_names),
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