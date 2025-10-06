import asyncio
from pathlib import PurePosixPath
from typing import Collection, List

import rich
import typer
from async_typer import AsyncTyper

from solradm.api.utils import validate_num_replicas, get_replicas, send_request
from solradm.commands.callbacks import add_verbosity_option
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.replica_position_filter import ReplicaPositionFilter
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.replica_type_filter import ReplicaTypeFilter
from solradm.commands.filters.shard_filter import ShardFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.commands.kube import load_configured_kubecontext
from solradm.completion.backups import backup_paths
from solradm.config import settings
from solradm.kube.utils import find_pods_by_node_name, run_command_in_pod
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader
from rich.filesize import decimal as human_readable_size
from rich.table import Table

app = AsyncTyper()
add_verbosity_option(app)


@app.async_command(help="Create backups for filtered replicas")
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def take(
        cluster_state: List[Collection],
        base_location_str: str = typer.Option(settings.get("backup_base_location", "/mnt/backups/backups"), "--location",
                                              help="Base location on each node's disk to place the backup. Each backup will be created under location/collection_name/shard_number",
                                              autocompletion=backup_paths),
        number_to_keep=typer.Option(None,
                                    help="Number of previous backups to keep. If more backups than the specified number exist in the directory, the oldest ones will be deleted."),
        create_directories=typer.Option(True,
                                        help="If set, required folders will be created via the kubecontext. This requires a kubecontext to be set, so set this to false and manually create the folders if you don't have one.")
):
    """Create backups of the specified shards."""

    replicas = validate_num_replicas(get_replicas(cluster_state))
    base_location = PurePosixPath(base_location_str)

    if create_directories:
        load_configured_kubecontext()
        overseer_pod = find_pods_by_node_name(get_overseer_leader())[0]
        rich.print(
            f"[text] Making sure backup directories exist on {base_location} through overseer-elected pod {overseer_pod.metadata.name}...")
        run_command_in_pod(overseer_pod.metadata.name,
                           f"mkdir -p {" ".join([str(base_location / f"{replica.shard.collection.name}/{replica.shard.name}") for replica in replicas])}")

    global_params = {"numberToKeep": number_to_keep}
    tasks = [
        MetaTask(
            [replica.shard.collection.name, replica.shard.name, replica.core],
            asyncio.create_task(send_request(get_overseer_leader(), f"/{replica.core}/replication",
                                             params={**global_params, "command": "backup",
                                                     "location": str(base_location / f"{replica.shard.collection.name}/{replica.shard.name}")}
                                             ),
                                ))
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["collection", "shard", "core"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))


@app.async_command(help="Restore backups. This command requires only one collection to be filtered!")
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def restore(
        cluster_state: List[Collection],
        backups_path_str: str = typer.Option(..., "--location",
                                             help="Directory which contains the shard directories. This directory must have subdirectories named shard1, shard2... which contain the backups.",
                                             autocompletion=backup_paths)):
    """Restore backups for the selected collection."""

    if len(cluster_state) > 1:
        rich.print(
            "[error] ❌ More than one collection has been filtered, and this command requires a singular collection or a part of it! ")
        raise typer.Exit(1)

    replicas = validate_num_replicas(get_replicas(cluster_state))
    backups_path = PurePosixPath(backups_path_str)

    tasks = [
        MetaTask(
            [replica.shard.collection.name, replica.shard.name, replica.core],
            asyncio.create_task(send_request(get_overseer_leader(), f"/{replica.core}/replication",
                                             params={"command": "restore",
                                                     "location": str(backups_path / replica.shard.name)}),
                                ))
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["collection", "shard", "core"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))


@app.async_command(name="restore-status", help="Show restore progress for the filtered replicas")
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def restore_status(cluster_state: List[Collection]):
    """Display restore status and on-disk usage for replicas currently restoring."""

    replicas = validate_num_replicas(get_replicas(cluster_state))

    status_tasks = [
        asyncio.create_task(
            send_request(
                get_overseer_leader(),
                f"/{replica.core}/replication",
                params={"command": "restorestatus"},
            )
        )
        for replica in replicas
    ]
    responses = await asyncio.gather(*status_tasks, return_exceptions=True)

    restoring_replicas = []
    for replica, response in zip(replicas, responses):
        if isinstance(response, Exception):
            rich.print(
                f"[warning]⚠️  Failed to fetch restore status for core {replica.core}: {response}"
            )
            continue

        status_payload = response.get("restorestatus") or {}
        status_value = status_payload.get("status")
        if not status_value or status_value.lower() != "in progress":
            continue

        restoring_replicas.append((replica, status_payload))

    if not restoring_replicas:
        rich.print("[success]✅  No restores are currently in progress for the selected replicas.")
        return

    kube_loaded = False
    try:
        kube_loaded = load_configured_kubecontext()
    except Exception as exc:  # pragma: no cover - defensive logging
        rich.print(f"[warning]⚠️  Failed to load configured kubecontext: {exc}")

    restore_rows = []
    for replica, payload in restoring_replicas:
        pod_name = None
        latest_dir = None
        latest_size: int | None = None
        if kube_loaded:
            try:
                pods = find_pods_by_node_name(replica.node_name)
            except Exception as exc:  # pragma: no cover - defensive logging
                rich.print(
                    f"[warning]⚠️  Failed to resolve pod for node {replica.node_name}: {exc}"
                )
                pods = []

            if not pods:
                rich.print(
                    f"[warning]⚠️  No pod found for node {replica.node_name}; unable to inspect restore directory"
                )
            else:
                pod_name = pods[0].metadata.name
                command = (
                    f"latest=$(ls -1dt /var/solr/data/{replica.core}/data/restore* 2>/dev/null | head -n 1); "
                    "if [ -z \"$latest\" ]; then echo 'MISSING'; "
                    "else size=$(du -sb \"$latest\" 2>/dev/null | awk '{print $1}'); "
                    "if [ -z \"$size\" ]; then size=0; fi; echo \"$size $latest\"; fi"
                )
                try:
                    output = await asyncio.to_thread(run_command_in_pod, pod_name, command)
                except Exception as exc:  # pragma: no cover - defensive logging
                    rich.print(
                        f"[warning]⚠️  Failed to inspect restore directory for core {replica.core} on {pod_name}: {exc}"
                    )
                else:
                    parsed = output.strip()
                    if parsed and parsed != "MISSING":
                        parts = parsed.split(maxsplit=1)
                        size_str = parts[0]
                        if size_str.isdigit():
                            latest_size = int(size_str)
                            latest_dir = parts[1] if len(parts) > 1 else None
                        else:
                            latest_dir = parts[-1]

        restore_rows.append(
            {
                "replica": replica,
                "payload": payload,
                "pod_name": pod_name,
                "latest_dir": latest_dir,
                "latest_size": latest_size,
            }
        )

    restore_rows.sort(
        key=lambda entry: entry["latest_size"] if entry["latest_size"] is not None else -1,
        reverse=True,
    )

    table = Table(title="Restore progress", header_style="bold magenta")
    table.add_column("Core", style="cyan", no_wrap=True)
    table.add_column("Node", style="green")
    table.add_column("Pod", style="green")
    table.add_column("Latest restore", style="yellow")
    table.add_column("Size", justify="right", style="magenta")
    table.add_column("Progress", style="white")

    for row in restore_rows:
        details = row["payload"].get("details") or {}
        progress = details.get("fileListDownloaded") or row["payload"].get("status") or "In Progress"
        size_text = "—"
        if row["latest_size"] is not None:
            try:
                size_text = human_readable_size(row["latest_size"])
            except Exception:  # pragma: no cover - formatting fallback
                size_text = str(row["latest_size"])

        table.add_row(
            row["replica"].core,
            row["replica"].node_name,
            row["pod_name"] or "—",
            row["latest_dir"] or "—",
            size_text,
            progress,
        )

    rich.print(table)
