from __future__ import annotations

import json
from typing import List, Sequence

import rich
import typer
from kazoo.client import KazooClient

from solradm.api.models import Collection, Replica, Shard
from solradm.api.utils import send_request
from solradm.commands.collections.reindex_engine import ReindexConfig, ReindexEngine
from solradm.commands.collections.reindex_ui import ReindexApp
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
from solradm.completion.contexts import context_names
from solradm.config.util import get_context


def _get_collection_from_zk(zk: str, collection: str) -> Collection:
    zk_client = KazooClient(hosts=zk, timeout=5)
    zk_client.start()
    try:
        data, _ = zk_client.get(f"/collections/{collection}/state.json")
    finally:
        zk_client.stop()
        zk_client.close()
    state = json.loads(data.decode("utf-8"))[collection]
    state["name"] = collection
    return Collection.model_validate(state)

def _resolve_collection(
    collection_name: str,
    *,
    cluster_state: Sequence[Collection],
    context,
    zk_override: str | None) -> Collection:
    try:
        if zk_override:
            return _get_collection_from_zk(zk_override, collection_name)
        if context:
            return _get_collection_from_zk(context.zk, collection_name)
    except Exception:
        return None

    for collection in cluster_state:
        if collection.name == collection_name:
            return collection

    return None

def _map_source_to_targets(
    source_shards: Sequence[Shard], target_shards: Sequence[Shard]
) -> dict[str, List[Shard]]:
    shard_map: dict[str, List[Shard]] = {}
    for idx, shard in enumerate(source_shards):
        target_name = target_shards[idx % len(target_shards)].name
        shard_map.setdefault(target_name, []).append(shard)
    return shard_map


def _leaders_by_shard(shards: Sequence[Shard]) -> dict[str, Replica | None]:
    return {shard.name: next((r for r in shard.replicas if r.leader), None) for shard in shards}


async def _detect_busy_shards(
    leaders: dict[str, Replica | None],
    handler: str,
) -> list[tuple[str, Replica]]:
    dataimport_handler = handler if handler.startswith("/") else f"/{handler}"
    busy: list[tuple[str, Replica]] = []
    for name, replica in leaders.items():
        if replica is None:
            continue
        if not replica.base_url:
            rich.print(f"[error]❌  Leader for shard {name} is missing a base URL")
            raise typer.Exit(1)
        status = await send_request(
            replica.base_url,
            f"/{replica.core}{dataimport_handler}",
            params={"command": "status", "wt": "json"},
        )
        if status.get("status") == "busy":
            busy.append((name, replica))
    return busy


async def _handle_busy_shards(
    busy_shards: list[tuple[str, Replica]],
    leaders: dict[str, Replica | None],
    handler: str,
) -> None:
    busy_count = len(busy_shards)
    rich.print(
        f"[warning]⚠️  Dataimport is already running on {busy_count} shard(s)."
    )
    open_ui = typer.confirm(
        "Open the textual UI to display dataimport progress?",
        default=True,
    )
    if open_ui:
        ui = ReindexApp(leaders=leaders, dataimport_handler=handler)
        await ui.run_async()
    raise typer.Exit(1)


@app.async_command(
    help="Reindex documents from a source collection into a target collection using the dataimport handler"
)
@with_cluster_state()
async def reindex(
    cluster_state: List[Collection],
    source_collection: str = typer.Option(
        ..., "--source", help="Collection to reindex from", autocompletion=collection_names
    ),
    target_collection: str = typer.Option(
        ..., "--target", help="Collection to reindex into", autocompletion=collection_names
    ),
    source_context: str | None = typer.Option(
        None, "--source-context", help="Context of the source collection", autocompletion=context_names
    ),
    source_zk: str | None = typer.Option(
        None,
        "--source-zk",
        help="ZooKeeper host where the source collection resides",
    ),
    target_zk: str | None = typer.Option(
        None,
        "--target-zk",
        help="ZooKeeper host where the target collection resides",
    ),
    target_context: str | None = typer.Option(
        None,
        "--target-context",
        help="Context of the target collection",
        autocompletion=context_names,
    ),
    handler: str = typer.Option("/dataimport", "--handler", help="Path of the dataimport handler"),
    fq: List[str] | None = typer.Option(
        None,
        "--fq",
        help="Filter query to pass to the dataimport handler",
    ),
    shards: str | None = typer.Option(
        None,
        "--shards",
        "--source-shard",
        help="Source shard numbers to reindex (e.g. '1,3-5,2+3-7,+4-16')",
        autocompletion=shard_numbers,
    ),
    all_shards: bool = typer.Option(
        False, "--all", help="Reindex all source shards"
    ),
    rows: int = typer.Option(2000, "--rows", help="How many rows to fetch per cursorMark request from the source collection."),
    sort: str = typer.Option("first_timestamp asc, item_id asc", help="Sort criteria for the cursorMark requests from the source collection"),
    qt: str = typer.Option("/dih", help="Request handler to fetch from the source collection."),
    fl: str = typer.Option("*,ignored_tmp1:_version_", help="Fields to reindex. By default, reindexes all fields."),
    timeout: int = typer.Option("300", help="The query timeout from the source collection, in seconds."),
) -> None:
    if (source_context and source_zk) or (target_context and target_zk):
        rich.print("[error]❌  Context and ZooKeeper overrides are mutually exclusive")
        raise typer.Exit(1)

    if all_shards and shards:
        rich.print("[error]❌  --shards and --all are mutually exclusive")
        raise typer.Exit(1)

    if not all_shards and not shards:
        rich.print("[error]❌  Either --shards or --all must be provided")
        raise typer.Exit(1)

    if (source_context):
        resolved_source_context = get_context(source_context)
        if not resolved_source_context:
            rich.print(f"[error]❌  Source context {source_context} not found")
            raise typer.Exit(1)
    else:
        resolved_source_context = None

    if (target_context):
        resolved_target_context = get_context(target_context)
        if not resolved_target_context:
            rich.print(f"[error]❌  Target context {target_context} not found")
            raise typer.Exit(1)
    else:
        resolved_target_context = None


    target_coll = _resolve_collection(
        target_collection,
        cluster_state=cluster_state,
        context=resolved_target_context,
        zk_override=target_zk)

    if not target_coll:
        rich.print(f"[error]❌  Target collection {target_collection} not found!")
        raise typer.Exit(1)

    source_coll = _resolve_collection(
        source_collection,
        cluster_state=cluster_state,
        context=resolved_source_context,
        zk_override=source_zk)

    if not source_coll:
        rich.print(f"[error]❌  Source collection {source_collection} not found!")
        raise typer.Exit(1)

    if all_shards:
        src_shards = sorted(source_coll.shards, key=lambda s: s.name)
    else:
        try:
            shard_rules = parse_shard_spec(shards)
        except typer.BadParameter as exc:
            rich.print(f"[error]❌  {exc}")
            raise typer.Exit(1)
        src_shards = sorted(
            (
                shard
                for shard in source_coll.shards
                if matches_shard_name(shard_rules, shard.name)
            ),
            key=lambda s: s.name,
        )
    if not src_shards:
        rich.print("[error]❌  No source shards matched")
        raise typer.Exit(1)

    tgt_shards = sorted(target_coll.shards, key=lambda s: s.name)
    if not tgt_shards:
        rich.print("[error]❌  Target collection has no shards")
        raise typer.Exit(1)

    shard_map = _map_source_to_targets(src_shards, tgt_shards)
    leaders = _leaders_by_shard(tgt_shards)
    busy_shards = await _detect_busy_shards(leaders, handler)
    if busy_shards:
        await _handle_busy_shards(busy_shards, leaders, handler)

    config = ReindexConfig(
        source_collection=source_collection,
        target_collection=target_collection,
        handler=handler,
        fq=fq,
        rows=rows,
        sort=sort,
        qt=qt,
        fl=fl,
        timeout=timeout,
    )
    engine = ReindexEngine(shard_map, leaders, config)
    ui = ReindexApp(engine)
    await ui.run_async()

    summary = engine.get_summary()
    if summary.failures:
        rich.print(f"[error]❌ {summary.failed_targets} target(s) failed:")
        for name, err in summary.failures:
            rich.print(f"  - {name}: {err}")
        raise typer.Exit(1)
    rich.print(f"[success]✅ Reindex completed: {summary.completed_targets} targets, {summary.total_docs_processed:,} docs")
