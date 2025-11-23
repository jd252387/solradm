from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Sequence

import rich
import typer
from rich.prompt import Confirm
from rich.table import Table

import solradm.api.utils as api_utils
from solradm.api.models import Collection, Replica, Shard
from solradm.api.state import get_collections, get_nodes_by_role
from solradm.api.utils import (
    get_replicas,
    send_request,
    validate_num_replicas,
)
from solradm.commands.collections.subapp import app
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.replica_position_filter import ReplicaPositionFilter
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.replica_type_filter import ReplicaTypeFilter
from solradm.commands.filters.shard_filter import ShardFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.completion.configs import config_names
from solradm.completion.nodes import node_names
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader


def _compile_node_patterns(
    patterns: Sequence[str] | None, option_display: str
) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise typer.BadParameter(
                f"Invalid regular expression for {option_display} '{pattern}': {exc}"
            ) from exc
    return compiled


def _select_nodes(
    available_nodes: Iterable[str],
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
) -> list[str]:
    nodes = list(available_nodes)
    include_regexes = _compile_node_patterns(include_patterns, "--node")
    exclude_regexes = _compile_node_patterns(exclude_patterns, "--exclude-node")

    def matches(node: str) -> bool:
        if include_regexes and not any(regex.search(node) for regex in include_regexes):
            return False
        if exclude_regexes and any(regex.search(node) for regex in exclude_regexes):
            return False
        return True

    return sorted({node for node in nodes if matches(node)})


def _sort_nodes(selected_nodes: Sequence[str], node_order: str) -> list[str]:
    if node_order == "alphabetical":
        return sorted(selected_nodes)

    if node_order == "numerical":
        sorted_nodes: list[tuple[int, str]] = []
        for node in selected_nodes:
            match = re.search(r"\d+", node)
            if not match:
                raise typer.BadParameter(
                    f"Selected node '{node}' lacks digits required for numerical ordering"
                )
            sorted_nodes.append((int(match.group()), node))

        return [node for _, node in sorted(sorted_nodes, key=lambda item: (item[0], item[1]))]

    raise typer.BadParameter("--node-order must be either 'alphabetical' or 'numerical'")


@app.async_command(
    help=(
        "Remove replicas for filtered collections.\n\n"
        "Examples:\n"
        "  solradm coll depopulate --collection '^logs-' --replica-state down --dry\n"
        "  solradm coll depopulate --collection '^metrics$' --shards 1-3 --replica-type leader --node 'solr0[12]'\n"
        "  solradm coll depopulate --collection '^analytics$' --replica-position 2 --exclude-node 'solr-backup'"
    )
)
@with_dry_run
@with_cluster_state(
    CollectionNameFilter,
    ShardFilter,
    ReplicaTypeFilter,
    ReplicaStateFilter,
    ReplicaPositionFilter,
)
async def depopulate(
    cluster_state: List[Collection],
    node: List[str] | None = typer.Option(
        None,
        "--node",
        help="Regex to select nodes",
        autocompletion=node_names,
    ),
    exclude_node: List[str] | None = typer.Option(
        None,
        "--exclude-node",
        help="Regex to exclude nodes",
        autocompletion=node_names,
    ),
    skip_checks: bool = typer.Option(False, "--skip-confirm", "-y", help="Skip confirmation prompt"),
) -> None:
    """Remove replicas from the selected collections."""

    replicas = get_replicas(cluster_state)

    if node or exclude_node:
        selected_nodes = _select_nodes(
            [replica.node_name for replica in replicas if replica.node_name],
            node,
            exclude_node,
        )

        if not selected_nodes:
            rich.print("[error] ❌ No nodes match the given selectors")
            raise typer.Exit(1)

        filtered_collections: list[Collection] = []
        filtered_replicas: list[Replica] = []
        for coll in cluster_state:
            new_shards: list[Shard] = []
            for shard in coll.shards:
                new_replicas = [
                    replica for replica in shard.replicas if replica.node_name in selected_nodes
                ]
                if new_replicas:
                    shard.replicas = new_replicas
                    new_shards.append(shard)
                    filtered_replicas.extend(new_replicas)
            if new_shards:
                coll.shards = new_shards
                filtered_collections.append(coll)

        cluster_state = filtered_collections
        replicas = filtered_replicas

        if not replicas:
            rich.print("[error] ❌ No replicas match the given node selectors")
            raise typer.Exit(1)

    if not skip_checks:
        table = Table(title="Cluster State", header_style="bold magenta")
        table.add_column("Collection", style="cyan")
        table.add_column("Active Shards", justify="right", style="green")
        table.add_column("Active Replicas", justify="right", style="green")
        table.add_column("Problematic Shards", justify="right", style="yellow")
        table.add_column("Problematic Replicas", justify="right", style="yellow")

        total_active_shards = 0
        total_active_replicas = 0
        total_non_active_shards = 0
        total_non_active_replicas = 0
        for coll in cluster_state:
            active_shards = sum(
                1 for shard in coll.shards if any(r.state == "active" for r in shard.replicas)
            )
            active_replicas = sum(
                1 for shard in coll.shards for r in shard.replicas if r.state == "active"
            )
            non_active_shards = sum(
                1 for shard in coll.shards if any(r.state != "active" for r in shard.replicas)
            )
            non_active_replicas = sum(
                1 for shard in coll.shards for r in shard.replicas if r.state != "active"
            )
            total_active_shards += active_shards
            total_active_replicas += active_replicas
            total_non_active_shards += non_active_shards
            total_non_active_replicas += non_active_replicas
            table.add_row(
                coll.name,
                str(active_shards),
                str(active_replicas),
                str(non_active_shards),
                str(non_active_replicas),
            )

        table.add_row(
            "[bold]TOTAL[/bold]",
            str(total_active_shards),
            str(total_active_replicas),
            str(total_non_active_shards),
            str(total_non_active_replicas),
            style="bold",
        )
        rich.print(table)

        if not Confirm.ask("Proceed with removing replicas?"):
            raise typer.Exit(0)

    replicas = validate_num_replicas(replicas)
    tasks = [
        MetaTask(
            [replica.shard.collection.name, replica.shard.name, replica.name],
            asyncio.create_task(
                send_request(
                    get_overseer_leader(),
                    "/admin/collections",
                    params={
                        "action": "DELETEREPLICA",
                        "collection": replica.shard.collection.name,
                        "shard": replica.shard.name,
                        "replica": replica.name,
                    },
                )
            ),
        )
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["collection", "shard", "replica"], tasks)
    await metatasks.gather_ignoring_errors(
        renderer=MultiTaskTable(metatasks, refresh_every=0.25)
    )


@app.async_command(
    help=(
        "Add replicas to a collection across selected nodes.\n\n"
        "Examples:\n"
        "  solradm coll populate --collection '^logs-' --shards 1-3 --node 'solr0[12]'\n"
        "  solradm coll populate --collection '^logs-' --exclude-shards 4-6 --node 'solr0[0-4]' --exclude-node 'solr03' --skip-checks --dry\n"
        "  solradm coll populate --collection '^logs-' --node 'solr0[0-4]' --node-order alphabetical"
    )
)
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter)
async def populate(
    cluster_state: List[Collection],
    node: List[str] | None = typer.Option(
        None,
        "--node",
        help="Regex to select nodes",
        autocompletion=node_names,
    ),
    exclude_node: List[str] | None = typer.Option(
        None,
        "--exclude-node",
        help="Regex to exclude nodes",
        autocompletion=node_names,
    ),
    node_order: str = typer.Option(
        "numerical",
        "--node-order",
        case_sensitive=False,
        help="Order to distribute nodes: numerical (default) or alphabetical",
        show_choices=True,
    ),
    skip_checks: bool = typer.Option(False, "--skip-confirm", "-y", help="Skip confirmation prompt"),
) -> None:
    """Populate a single collection with replicas across nodes."""

    if len(cluster_state) != 1:
        rich.print(
            "[error] ❌ More than one collection has been filtered, and this command requires a singular collection!"
        )
        raise typer.Exit(1)

    collection = cluster_state[0]
    shards = collection.shards

    data_nodes = get_nodes_by_role("data").get("on", [])

    selected_nodes = _select_nodes(data_nodes, node, exclude_node)

    if node_order.lower() not in {"alphabetical", "numerical"}:
        raise typer.BadParameter("--node-order must be either 'alphabetical' or 'numerical'")

    selected_nodes = _sort_nodes(selected_nodes, node_order.lower())

    if not selected_nodes:
        rich.print("[error] ❌ No nodes match the given selectors")
        raise typer.Exit(1)

    shards_sorted = sorted(shards, key=lambda s: int(re.findall(r"\d+", s.name)[0]))

    num_nodes = len(selected_nodes)
    num_shards = len(shards_sorted)
    base = num_shards // num_nodes
    remainder = num_shards % num_nodes

    node_to_shards: dict[str, list[Shard]] = {}
    idx = 0
    for i, n in enumerate(selected_nodes):
        count = base + (1 if i < remainder else 0)
        if count:
            node_to_shards[n] = shards_sorted[idx : idx + count]
            idx += count

    if not node_to_shards:
        rich.print("[warning] ⚠️ No replicas need to be created on the selected nodes")
        raise typer.Exit(0)

    if not skip_checks:
        table = Table(title="Planned replicas to add")
        table.add_column("Node")
        table.add_column("Shards")
        for n, shards_list in node_to_shards.items():
            table.add_row(n, ", ".join(s.name for s in shards_list))
        rich.print(table)

        counts = [len(shards_list) for shards_list in node_to_shards.values()]
        avg = sum(counts) / len(counts)
        dist = Counter(counts)

        unused_nodes = sorted(set(selected_nodes) - set(node_to_shards.keys()))

        rich.print(f"Average replicas per node: {avg:.2f}")

        dist_table = Table(title="Replica distribution")
        dist_table.add_column("Replicas")
        dist_table.add_column("Nodes")
        for num, cnt in sorted(dist.items()):
            dist_table.add_row(str(num), str(cnt))
        rich.print(dist_table)

        if unused_nodes:
            rich.print(
                "[warning] ⚠️ The following nodes matched the filter but will not receive replicas: "
                + ", ".join(unused_nodes)
            )

        if not Confirm.ask("Proceed with adding replicas?"):
            raise typer.Exit(0)

    tasks: list[MetaTask] = []
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
    await metatasks.gather_ignoring_errors(
        renderer=MultiTaskTable(metatasks, refresh_every=0.25)
    )


@app.async_command(
    help=(
        "Create a new collection.\n\n"
        "Examples:\n"
        "  solradm coll create search --shards 4 --conf search-config\n"
        "  solradm coll create metrics --shards 6 --upload-conf ./configs/metrics --populate --node 'solr0[1-3]' --node-order alphabetical --dry"
    )
)
@with_dry_run
async def create(
    name: str = typer.Argument(..., help="Name of the collection"),
    shards: int = typer.Option(..., "--shards", help="Number of shards"),
    conf: str | None = typer.Option(
        None,
        "--conf",
        help="Configuration name in ZooKeeper",
        autocompletion=config_names,
    ),
    upload_conf: Path | None = typer.Option(
        None,
        "--upload-conf",
        exists=False,
        resolve_path=False,
        help="Path or configset name to upload before creation",
    ),
    populate_after: bool = typer.Option(
        False, "--populate", help="Populate the collection after creation"
    ),
    node: str | None = typer.Option(
        None,
        "--node",
        help="Regex to select nodes for populate",
        autocompletion=node_names,
    ),
    node_order: str = typer.Option(
        "numerical",
        "--node-order",
        case_sensitive=False,
        help="Order to distribute nodes during populate: numerical (default) or alphabetical",
        show_choices=True,
    ),
) -> None:
    """Create a collection in Solr."""

    if upload_conf:
        if conf:
            raise typer.BadParameter("You can't specify both --conf and --upload-conf!")
        from solradm.commands.zk.editor import upload

        upload(
            paths=[str(upload_conf)],
            znode_path="/configs",
            only_used=False,
            reload=False,
            exclude=None,
            skip_checks=True,
        )
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
        await populate(
            dry_run=api_utils.is_dry_run,
            collection_name_filter=f"^{name}$",
            node=[node] if node else None,
            exclude_node=None,
            node_order=node_order,
        )


@app.async_command(help="Delete collections matching a pattern")
@with_dry_run
async def delete(
    pattern: str = typer.Argument(..., help="Regex pattern for collection names"),
) -> None:
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
