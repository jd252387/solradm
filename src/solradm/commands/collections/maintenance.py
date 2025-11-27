from __future__ import annotations

import asyncio
from collections import Counter
from typing import List

import rich
import typer
from rich.prompt import Confirm
from rich.table import Table

import solradm.api.utils as api_utils
from solradm.api.models import Collection, Replica
from solradm.api.state import get_nodes_by_role
from solradm.api.utils import get_replicas, send_request, validate_num_replicas
from solradm.commands.collections.subapp import app
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.replica_position_filter import ReplicaPositionFilter
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.replica_type_filter import ReplicaTypeFilter
from solradm.commands.filters.shard_filter import ShardFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask


@app.async_command(
    help=(
        "Reload cores for filtered replicas.\n\n"
        "Examples:\n"
        "  solradm coll reload --collection '^logs-' --dry\n"
        "  solradm coll reload --collection '^logs-' --shards 1-2 --replica-type leader --replica-state active\n"
        "  solradm coll reload --collection '^analytics$' --replica-position 1 --exclude-replica-type follower --coordinators true"
    )
)
@with_dry_run
@with_cluster_state(
    CollectionNameFilter,
    ShardFilter,
    ReplicaTypeFilter,
    ReplicaStateFilter,
    ReplicaPositionFilter,
    show_filter_explanations=True,
)
async def reload(
    cluster_state: List[Collection],
    coordinators: bool = typer.Option(
        None,
        help="If unset, reloads both data and coordinator nodes. If set to true, only reload coordinators. If set to false, only reload data nodes.",
    ),
    skip_checks: bool = typer.Option(False, "--skip-confirm", "-y", help="Skip confirmation prompt"),
) -> None:
    """Reload the specified cores and optionally coordinators."""

    replicas: List[Replica] = []
    collection_counts: Counter[str] = Counter()
    collection_configsets = {
        collection.name: collection.configName for collection in cluster_state
    }
    selected_collection_names = set(collection_configsets.keys())
    selected_configset_names = set(collection_configsets.values())
    if coordinators is None or not coordinators:
        data_replicas = get_replicas(cluster_state)
        replicas.extend(data_replicas)
        for replica in data_replicas:
            if replica.shard and replica.shard.collection:
                collection_counts[replica.shard.collection.name] += 1
    if coordinators is None or coordinators:
        coordinator_nodes = get_nodes_by_role("coordinator")["on"]
        for node in coordinator_nodes:
            cores = await api_utils.get_cores_from_node(node)
            for core in cores:
                collection_name = core.cloud.collection
                configset_name = collection_configsets.get(collection_name)
                if (
                    collection_name not in selected_collection_names
                    or configset_name not in selected_configset_names
                ):
                    continue
                replicas.append(
                    Replica(
                        name=core.name,
                        core=core.name,
                        node_name=node,
                        type=core.cloud.replicaType,
                        state=core.lastPublished,
                        leader=True,
                        force_set_state=False,
                        base_url=node,
                    )
                )
                collection_counts[core.cloud.collection] += 1

    replicas = validate_num_replicas(replicas)

    if collection_counts and not skip_checks:
        table = Table(title="Planned core reloads", header_style="bold magenta")
        table.add_column("Collection", style="cyan")
        table.add_column("Cores", justify="right", style="green")

        total = 0
        for collection_name in sorted(collection_counts):
            count = collection_counts[collection_name]
            total += count
            table.add_row(collection_name, str(count))

        table.add_row("[bold]TOTAL[/bold]", str(total), style="bold")
        rich.print(table)

        if not Confirm.ask("Proceed with reloading the listed cores?"):
            raise typer.Exit(0)

    tasks = [
        MetaTask(
            [replica.base_url, replica.core],
            asyncio.create_task(
                send_request(
                    replica.base_url,
                    "/admin/cores",
                    params={"action": "RELOAD", "core": replica.core},
                )
            ),
        )
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["host", "core"], tasks)
    await metatasks.gather_ignoring_errors(
        renderer=MultiTaskTable(metatasks, refresh_every=0.25)
    )
