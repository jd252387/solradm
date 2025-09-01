from async_typer import AsyncTyper
from rich.console import Console
from rich.table import Table

from solradm.api.state import get_collections

app = AsyncTyper()


@app.async_command()
async def overview():
    """Show counts of active and offline shards and replicas for each collection."""
    collections = get_collections()

    table = Table(title="Cluster Status")
    table.add_column("Collection", style="cyan")
    table.add_column("Active Shards", justify="right", style="green")
    table.add_column("Offline Shards", justify="right", style="red")
    table.add_column("Active Replicas", justify="right", style="green")
    table.add_column("Offline Replicas", justify="right", style="red")

    total_active_shards = 0
    total_offline_shards = 0
    total_active_replicas = 0
    total_offline_replicas = 0

    for coll in collections:
        active_replicas = sum(
            1 for shard in coll.shards for replica in shard.replicas if replica.state == "active"
        )
        offline_replicas = sum(
            1 for shard in coll.shards for replica in shard.replicas if replica.state != "active"
        )
        active_shards = sum(
            1 for shard in coll.shards if any(replica.state == "active" for replica in shard.replicas)
        )
        offline_shards = len(coll.shards) - active_shards

        total_active_replicas += active_replicas
        total_offline_replicas += offline_replicas
        total_active_shards += active_shards
        total_offline_shards += offline_shards

        table.add_row(
            coll.name,
            str(active_shards),
            str(offline_shards),
            str(active_replicas),
            str(offline_replicas),
        )

    table.add_row(
        "[bold]TOTAL[/bold]",
        str(total_active_shards),
        str(total_offline_shards),
        str(total_active_replicas),
        str(total_offline_replicas),
        style="bold",
    )

    Console().print(table)
