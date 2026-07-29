import re
from typing import List

import rich
import typer
from rich.table import Table

from solradm.api.models import Collection
from solradm.api.state import get_aliases
from solradm.commands.collections.subapp import app
from solradm.commands.filters.utils import with_cluster_state
from solradm.completion.collections import collection_names


@app.command("list", help="List all collections with their aliases and configset.")
@with_cluster_state(allow_empty=True)
def list_collections(
        cluster_state: List[Collection],
        collection: str | None = typer.Option(
            None,
            "--collection",
            "-c",
            help="Regex pattern to only show collections whose name matches",
            autocompletion=collection_names,
        ),
):
    """Display a table of collections, their aliases and their configuration."""

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

    aliases = get_aliases()

    table = Table(title="Collections", header_style="bold magenta")
    table.add_column("Collection", style="cyan")
    table.add_column("Aliases", style="cyan")
    table.add_column("Config")

    for coll in sorted(cluster_state, key=lambda c: c.name):
        table.add_row(
            coll.name,
            ", ".join(aliases.get(coll.name, [])) or "-",
            coll.configName,
        )

    rich.print(table)
