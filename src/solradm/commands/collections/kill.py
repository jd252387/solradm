from __future__ import annotations

import asyncio
from typing import List

import rich
import typer
from kubernetes.client import CoreV1Api
from rich.prompt import Confirm
from rich.table import Table

import solradm.api.utils as api_utils
from solradm.api.models import Collection
from solradm.api.utils import get_replicas
from solradm.commands.collections.subapp import app
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.node_name_filter import NodeNameFilter
from solradm.commands.filters.replica_position_filter import ReplicaPositionFilter
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.replica_type_filter import ReplicaTypeFilter
from solradm.commands.filters.shard_filter import ShardFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.config.util import get_current_context
from solradm.kube.utils import find_pods_by_node_name, get_kube_context_info
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask


@app.command(
    help=(
        "Delete the Kubernetes pods hosting the selected Solr cores.\n\n"
        "Examples:\n"
        "  solradm coll kill --collection '^logs-' --replica-state down --dry\n"
        "  solradm coll kill --collection '^metrics$' --shards 1-3 --replica-type leader --node 'solr0[12]'\n"
        "  solradm coll kill --collection '^analytics$' --replica-position 2 --exclude-node 'solr-backup'"
    )
)
@with_dry_run
@with_cluster_state(
    CollectionNameFilter,
    ShardFilter,
    ReplicaTypeFilter,
    ReplicaStateFilter,
    ReplicaPositionFilter,
    NodeNameFilter,
)
async def kill(
    cluster_state: List[Collection],
    skip_checks: bool = typer.Option(False, "--skip-confirm", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete the Kubernetes pods hosting the selected Solr cores."""

    replicas = get_replicas(cluster_state)

    if len(replicas) == 0:
        rich.print("[info]No cores selected; nothing to kill.")
        return

    kube = get_kube_context_info(get_current_context())

    # Translate each selected core's node to the pod hosting it, collapsing
    # multiple cores that share a pod into a single deletion.
    pod_to_cores: dict[str, list[str]] = {}
    for replica in replicas:
        pods = find_pods_by_node_name(kube, replica.node_name)
        if not pods:
            rich.print(
                f"[warning]⚠️  No pod found for node {replica.node_name} "
                f"(core {replica.core}); skipping"
            )
            continue
        pod_name = pods[0].metadata.name
        pod_to_cores.setdefault(pod_name, []).append(replica.core)

    if not pod_to_cores:
        rich.print("[error]❌ No pods resolved for the selected cores.")
        raise typer.Exit(1)

    if not skip_checks:
        table = Table(title="Pods to kill", header_style="bold magenta")
        table.add_column("Pod", style="cyan")
        table.add_column("Cores", justify="right", style="green")
        for pod_name, cores in sorted(pod_to_cores.items()):
            table.add_row(pod_name, str(len(cores)))
        table.add_row("[bold]TOTAL[/bold]", str(len(pod_to_cores)), style="bold")
        rich.print(table)

        if not Confirm.ask("Proceed with deleting the above pods?"):
            raise typer.Exit(0)

    if api_utils.is_dry_run:
        rich.print("[info]💡 Dry run: no pods were deleted.")
        return

    v1 = CoreV1Api(kube.api_client)
    tasks = [
        MetaTask(
            [pod_name],
            asyncio.create_task(
                asyncio.to_thread(v1.delete_namespaced_pod, pod_name, kube.namespace)
            ),
        )
        for pod_name in sorted(pod_to_cores)
    ]
    metatasks = MultiMetaTask(["pod"], tasks)
    await metatasks.gather_ignoring_errors(
        renderer=MultiTaskTable(metatasks, refresh_every=0.25)
    )
