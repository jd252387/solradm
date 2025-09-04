import asyncio
from pathlib import PurePosixPath
from typing import List, Collection

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
from solradm.kube.utils import find_pods_by_node_name, run_command_in_pod
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader
from solradm.config import settings
from solradm.completion.backups import backup_paths

app = AsyncTyper()
add_verbosity_option(app)


@app.async_command(help="Create backups for filtered replicas")
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def take(
        cluster_state: List[Collection],
        base_location_str: str = typer.Option(settings.get("backup_base_location", "/mnt/backups"), "--location",
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
                                                     "location": base_location / f"{replica.shard.collection.name}/{replica.shard.name}"}
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
                                                     "location": backups_path / replica.shard.name}),
                                ))
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["collection", "shard", "core"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))
