import asyncio
import re
from typing import List, TYPE_CHECKING, Any

import typer
from async_typer import AsyncTyper
from solradm.lazy import lazy_module

from solradm import completion
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.kube.utils import (
    find_pods_by_node_name,
    get_configured_kubecontext,
    run_command_in_pod,
    switch_current_kubecontext,
)
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader

rich = lazy_module("rich")
api_utils = lazy_module("solradm.api.utils")
api_state = lazy_module("solradm.api.state")

if TYPE_CHECKING:  # pragma: no cover
    from solradm.api.models import Collection

app = AsyncTyper()


@app.async_command(help="Remove cores not belonging to selected collections from nodes")
@with_dry_run
@with_cluster_state(CollectionNameFilter)
async def drain(
    cluster_state: List[Any],
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
    """Drain a node of cores that do not belong to the selected collections.

    The command will also remove leftover index directories on the node's disk
    that are not associated with any core.
    """

    allowed_collections = {c.name for c in cluster_state}

    data_nodes = api_state.get_nodes_by_role("data").get("on", [])
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

    # Delete replicas that do not belong to allowed collections
    delete_tasks: List[MetaTask] = []
    for n in selected_nodes:
        cores = await api_utils.get_cores_from_node(n)
        for core in cores:
            if core.cloud.collection not in allowed_collections:
                delete_tasks.append(
                    MetaTask(
                        [n, core.cloud.collection, core.cloud.shard, core.cloud.replica],
                        asyncio.create_task(
                            api_utils.send_request(
                                get_overseer_leader(),
                                "/admin/collections",
                                params={
                                    "action": "DELETEREPLICA",
                                    "collection": core.cloud.collection,
                                    "shard": core.cloud.shard,
                                    "replica": core.cloud.replica,
                                },
                            )
                        ),
                    )
                )

    if delete_tasks:
        metatasks = MultiMetaTask(["node", "collection", "shard", "replica"], delete_tasks)
        await metatasks.gather_ignoring_errors(
            renderer=MultiTaskTable(metatasks, refresh_every=0.25)
        )

    # After deletion, remove leftover directories from disk
    configured = get_configured_kubecontext()
    if not configured:
        rich.print("[warning] ⚠️ No kubecontext configured; skipping disk cleanup")
        return

    switch_current_kubecontext(configured)

    for n in selected_nodes:
        pods = find_pods_by_node_name(n)
        if not pods:
            rich.print(f"[warning] ⚠️ No pod found for node {n}, skipping disk cleanup")
            continue
        pod_name = pods[0].metadata.name

        remaining_cores = await api_utils.get_cores_from_node(n)
        core_names = {core.name for core in remaining_cores}

        dirs_raw = run_command_in_pod(pod_name, "ls -1 /var/solr/data").split()
        for d in dirs_raw:
            if d not in core_names:
                has_index = (
                    run_command_in_pod(
                        pod_name,
                        f"test -d /var/solr/data/{d}/index && echo yes || echo no",
                    ).strip()
                    == "yes"
                )
                if has_index:
                    rich.print(f"[text] Removing directory {d} from node {n}")
                    if not api_utils.is_dry_run:
                        run_command_in_pod(pod_name, f"rm -rf /var/solr/data/{d}")

