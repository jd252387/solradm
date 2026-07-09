from __future__ import annotations

from pathlib import Path
from typing import List

import rich
import typer

from solradm.api.models import Collection, Replica
from solradm.api.utils import get_replicas
from solradm.commands.collections.subapp import app
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.node_name_filter import NodeNameFilter
from solradm.commands.filters.replica_position_filter import ReplicaPositionFilter
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.replica_type_filter import ReplicaTypeFilter
from solradm.commands.filters.shard_filter import ShardFilter
from solradm.commands.filters.utils import with_cluster_state
from solradm.config.util import get_current_context
from solradm.kube.utils import find_pods_by_node_name, get_kube_context_info

VALID_FIELDS = (
    "node",
    "pod",
    "core",
    "config",
    "shard",
    "collection",
    "replica_state",
)


def _field_value(field: str, replica: Replica, node_to_pod: dict[str, str]) -> str:
    """Resolve a single ``-o`` field name to its string value for a core."""
    if field == "node":
        return replica.node_name
    if field == "pod":
        return node_to_pod.get(replica.node_name, "")
    if field == "core":
        return replica.core
    if field == "config":
        return replica.shard.collection.configName
    if field == "shard":
        return replica.shard.name
    if field == "collection":
        return replica.shard.collection.name
    if field == "replica_state":
        return replica.state
    # Unreachable: fields are validated before this helper is called.
    raise typer.BadParameter(f"Unknown output field '{field}'")


@app.command(
    "export-state",
    help=(
        "Write selected Solr cores' fields to a file, one core per line.\n\n"
        "Each selected core produces one line containing the requested fields, "
        "space-separated, in the order the --output options are given.\n\n"
        "Examples:\n"
        "  solradm coll export-state cores.txt -o node -o core --collection '^logs-'\n"
        "  solradm coll export-state out.txt -o pod -o shard -o replica_state --replica-state down"
    ),
)
@with_cluster_state(
    CollectionNameFilter,
    ShardFilter,
    ReplicaTypeFilter,
    ReplicaStateFilter,
    ReplicaPositionFilter,
    NodeNameFilter,
)
async def export_state(
    cluster_state: List[Collection],
    file_name: Path = typer.Argument(..., help="File to write the exported state to"),
    output: List[str] = typer.Option(
        ...,
        "--output",
        "-o",
        help=(
            "Field(s) to write per core. Repeat to include multiple. "
            "One of: " + ", ".join(VALID_FIELDS)
        ),
    ),
) -> None:
    """Export fields for the selected Solr cores to a file."""

    invalid = [field for field in output if field not in VALID_FIELDS]
    if invalid:
        raise typer.BadParameter(
            f"Invalid --output field(s): {', '.join(invalid)}. "
            f"Valid fields are: {', '.join(VALID_FIELDS)}"
        )

    replicas = get_replicas(cluster_state)

    if len(replicas) == 0:
        rich.print("[info]No cores selected; nothing to export.")
        return

    # Resolve node -> pod names only if the user asked for the pod field, caching
    # each node so the Kubernetes API is queried at most once per node.
    node_to_pod: dict[str, str] = {}
    if "pod" in output:
        kube = get_kube_context_info(get_current_context())
        for replica in replicas:
            if replica.node_name in node_to_pod:
                continue
            pods = find_pods_by_node_name(kube, replica.node_name)
            if not pods:
                rich.print(
                    f"[warning]⚠️  No pod found for node {replica.node_name} "
                    f"(core {replica.core})"
                )
                node_to_pod[replica.node_name] = ""
            else:
                node_to_pod[replica.node_name] = pods[0].metadata.name

    file_name.parent.mkdir(parents=True, exist_ok=True)
    with file_name.open("w", encoding="utf-8") as fh:
        for replica in replicas:
            line = " ".join(_field_value(field, replica, node_to_pod) for field in output)
            fh.write(line + "\n")

    rich.print(
        f"[success]✅ Wrote {len(replicas)} core(s) to {file_name}"
    )
