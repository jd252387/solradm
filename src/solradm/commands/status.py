from typing import List
from typing import List, TYPE_CHECKING, Any

import rich
import typer
from rich.table import Table
from solradm.lazy import lazy_module

from solradm.commands.filters.utils import with_cluster_state

api_utils = lazy_module("solradm.api.utils")

if TYPE_CHECKING:  # pragma: no cover
    from solradm.api.models import Collection


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


@with_cluster_state()
def status(
    cluster_state: List[Any],
    severity: List[str] | None = typer.Option(
        None,
        "--severity",
        "-s",
        help="Only show replicas with these severities",
    ),
    show_max: int = typer.Option(20, "--show", help="Show this amount of top rows")
):
    """Display status table for all replicas across collections."""

    replicas = api_utils.get_replicas(cluster_state)

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
