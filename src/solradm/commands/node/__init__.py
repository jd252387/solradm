import asyncio
import re
import webbrowser
from typing import List

import rich
import typer
from async_typer import AsyncTyper

import solradm.api.utils as api_utils
from solradm.api.models import Collection
from solradm.api.state import get_nodes_by_role
from solradm.api.utils import get_cores_from_node, send_request
from solradm.api.utils import get_host_with_scheme
from solradm.commands.callbacks import add_verbosity_option
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.completion.nodes import node_names
from solradm.config.util import get_current_context
from solradm.kube.utils import (
    find_pods_by_node_name,
    get_kube_context_info,
    run_command_in_pod,
)
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader

app = AsyncTyper()
add_verbosity_option(app)


@app.async_command(help="Remove cores not belonging to selected collections from nodes")
@with_dry_run
@with_cluster_state(CollectionNameFilter)
async def drain(
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
):
    """Drain a node of cores that do not belong to the selected collections.

    The command will also remove leftover index directories on the node's disk
    that are not associated with any core.
    """

    allowed_collections = {c.name for c in cluster_state}

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

    # Delete replicas that do not belong to allowed collections
    delete_tasks: List[MetaTask] = []
    for n in selected_nodes:
        cores = await get_cores_from_node(n)
        for core in cores:
            if core.cloud.collection not in allowed_collections:
                delete_tasks.append(
                    MetaTask(
                        [n, core.cloud.collection, core.cloud.shard, core.cloud.replica],
                        asyncio.create_task(
                            send_request(
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

    kube = None
    try:
        kube = get_kube_context_info(get_current_context())
    except Exception:
        rich.print("[warning] ⚠️ No kubecontext configured; skipping disk cleanup")
        return

    for n in selected_nodes:
        pods = find_pods_by_node_name(kube, n)
        if not pods:
            rich.print(f"[warning] ⚠️ No pod found for node {n}, skipping disk cleanup")
            continue
        pod_name = pods[0].metadata.name

        remaining_cores = await get_cores_from_node(n)
        core_names = {core.name for core in remaining_cores}

        dirs_raw = run_command_in_pod(kube, pod_name, "ls -1 /var/solr/data").split()
        for d in dirs_raw:
            if d not in core_names:
                has_index = (
                        run_command_in_pod(
                            kube,
                            pod_name,
                            f"test -d /var/solr/data/{d}/index && echo yes || echo no",
                        ).strip()
                        == "yes"
                )
                if has_index:
                    rich.print(f"[text] Removing directory {d} from node {n}")
                    if not api_utils.is_dry_run:
                        run_command_in_pod(kube, pod_name, f"rm -rf /var/solr/data/{d}")


@app.command(help="Open the overseer-elected node's Solr UI in a browser")
def ui():
    """Open the current overseer node in the default browser."""
    url = get_host_with_scheme(get_overseer_leader(), "http") + "/solr/#/"
    webbrowser.open(url)
    rich.print(f"[success]✅  Opened {url}")
