from typing import List

import rich
import typer
from rich.table import Table

from solradm.api.models import Collection
from solradm.api.utils import get_replicas
from solradm.commands.filters.utils import with_cluster_state

# Mapping of replica states to severity for sorting
SEVERITY_ORDER = {
    "gone": 0,
    "down": 1,
    "recovery": 2,
    "recovery_failed": 3,
    "inactive": 4,
    "active": 5,
}

# Colors for states
STATE_COLORS = {
    "active": "green",
    "recovery": "yellow",
    "down": "red",
    "gone": "red",
    "recovery_failed": "magenta",
    "inactive": "bright_black",
}


@with_cluster_state(allow_empty=True)
def status(
        cluster_state: List[Collection],
        severity: List[str] | None = typer.Option(
            None,
            "--severity",
            "-s",
            help="Only show replicas with these severities",
        ),
        show_max: int = typer.Option(20, "--show", help="Show this amount of top rows")
):
    """Display status table for all replicas across collections."""

    replicas = get_replicas(cluster_state)

    if severity:
        allowed = {s.lower() for s in severity}
        replicas = [r for r in replicas if r.state.lower() in allowed]

    replicas.sort(key=lambda r: SEVERITY_ORDER.get(r.state.lower(), len(SEVERITY_ORDER)))

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
