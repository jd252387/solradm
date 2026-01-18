from __future__ import annotations

import asyncio
from typing import List

import rich
import typer

from solradm.api.models import Collection, Replica, Shard
from solradm.api.utils import send_request
from solradm.commands.collections.subapp import app
from solradm.commands.filters.shard_utils import (
    matches_shard_name,
    parse_shard_spec,
)
from solradm.commands.filters.utils import with_cluster_state
from solradm.completion.collections import (
    collection_names,
    shard_numbers,
)


def _leaders_by_shard(shards: List[Shard]) -> dict[str, Replica | None]:
    return {shard.name: next((r for r in shard.replicas if r.leader), None) for shard in shards}


@app.async_command(
    help="Abort a running reindex operation on a collection using the dataimport handler"
)
@with_cluster_state()
async def abort_reindex(
    cluster_state: List[Collection],
    collection: str = typer.Option(
        ..., "--collection", "-c", help="Collection to abort reindex on", autocompletion=collection_names
    ),
    handler: str = typer.Option("/dataimport", "--handler", help="Path of the dataimport handler"),
    shards: str | None = typer.Option(
        None,
        "--shards",
        help="Shard numbers to abort reindex on (e.g. '1,3-5,2+3-7,+4-16')",
        autocompletion=shard_numbers,
    ),
    all_shards: bool = typer.Option(
        False, "--all", help="Abort reindex on all shards"
    ),
) -> None:
    if all_shards and shards:
        rich.print("[error]❌  --shards and --all are mutually exclusive")
        raise typer.Exit(1)

    if not all_shards and not shards:
        rich.print("[error]❌  Either --shards or --all must be provided")
        raise typer.Exit(1)

    # Find the collection
    target_coll = None
    for coll in cluster_state:
        if coll.name == collection:
            target_coll = coll
            break

    if not target_coll:
        rich.print(f"[error]❌  Collection {collection} not found!")
        raise typer.Exit(1)

    # Get the shards to abort on
    if all_shards:
        target_shards = sorted(target_coll.shards, key=lambda s: s.name)
    else:
        try:
            shard_rules = parse_shard_spec(shards)
        except typer.BadParameter as exc:
            rich.print(f"[error]❌  {exc}")
            raise typer.Exit(1)
        target_shards = sorted(
            (
                shard
                for shard in target_coll.shards
                if matches_shard_name(shard_rules, shard.name)
            ),
            key=lambda s: s.name,
        )

    if not target_shards:
        rich.print("[error]❌  No shards matched")
        raise typer.Exit(1)

    leaders = _leaders_by_shard(target_shards)
    dataimport_path = f"/{collection}{handler}"

    async def abort_shard(shard_name: str) -> None:
        leader = leaders.get(shard_name)
        if not leader or not leader.base_url:
            rich.print(f"[error]❌  No leader with a base URL found for shard {shard_name}")
            raise typer.Exit(1)

        try:
            await send_request(
                leader.base_url,
                dataimport_path,
                params={"command": "abort", "wt": "json"},
            )
            rich.print(f"[success]✅  Aborted reindex on shard {shard_name}")
        except Exception as e:
            rich.print(f"[error]❌  Failed to abort reindex on shard {shard_name}: {e}")
            raise

    await asyncio.gather(*(abort_shard(shard.name) for shard in target_shards))

    rich.print(f"[success]✅  Abort command sent to {len(target_shards)} shard(s)")
