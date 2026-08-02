import re
from typing import List

import rich
import typer
from rich.table import Table

from solradm.api.models import Collection
from solradm.api.utils import get_replicas
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.utils import with_cluster_state
from solradm.completion.collections import collection_names

# Worst-first ordering of replica states for the table
STATE_ORDER = {
    "gone": 0,
    "down": 1,
    "recovering": 2,
    "recovery_failed": 3,
    "inactive": 4,
    "active": 5,
}

# Colors for states
STATE_COLORS = {
    "active": "green",
    "recovering": "yellow",
    "down": "red",
    "gone": "red",
    "recovery_failed": "magenta",
    "inactive": "bright_black",
}


@with_cluster_state(ReplicaStateFilter, allow_empty=True)
def status(
        cluster_state: List[Collection],
        collection: str | None = typer.Option(
            None,
            "--collection",
            "-c",
            help="Regex pattern to only show collections whose name matches",
            autocompletion=collection_names,
        ),
        show_max: int = typer.Option(20, "--show", help="Show this amount of top rows")
):
    """Display status table for all replicas across collections.

    Examples:

      solradm status --replica-state down --replica-state recovering

      solradm status --exclude-replica-state active --show 50

      solradm status --collection '^logs-' --replica-state recovery_failed
    """

    if not cluster_state:
        rich.print("[warning]ℹ️  No collections found in the cluster.")
        return

    if collection:
        try:
            pattern = re.compile(collection)
        except re.error as e:
            raise typer.BadParameter(f"Invalid regex pattern '{collection}': {e}")
        cluster_state = [c for c in cluster_state if pattern.search(c.name)]

        if not cluster_state:
            rich.print(f"[warning]ℹ️  No collections matched the pattern '{collection}'.")
            return

    replicas = get_replicas(cluster_state)
    replicas.sort(key=lambda r: STATE_ORDER.get(r.state.lower(), len(STATE_ORDER)))

    table = Table(title="Replica Status", header_style="bold magenta")
    table.add_column("Collection", style="cyan")
    table.add_column("Shard", style="cyan")
    table.add_column("Replica", style="cyan")
    table.add_column("Node", style="cyan")
    table.add_column("State")

    for i, replica in enumerate(replicas):
        if i == show_max:
            break
        state = replica.state.lower()
        color = STATE_COLORS.get(state, "white")
        table.add_row(
            replica.shard.collection.name,
            replica.shard.name,
            replica.name,
            replica.node_name,
            f"[{color}]{replica.state}[/]",
        )

    rich.print(table)
