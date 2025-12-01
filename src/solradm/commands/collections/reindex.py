from __future__ import annotations

import asyncio
import json
import re
from contextlib import contextmanager
from typing import Iterator, List, Sequence

import rich
import typer
from kazoo.client import KazooClient
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

from solradm.api.models import Collection, Replica, Shard
from solradm.api.utils import get_host_with_scheme, send_request
from solradm.commands.collections.subapp import app
from solradm.commands.filters.utils import with_cluster_state
from solradm.completion.collections import collection_names, source_collection_names
from solradm.completion.contexts import context_names
from solradm.config import settings
from solradm.config.util import get_context


def _parse_status(json_resp: dict) -> tuple[int, int | None, str | None]:
    msgs = json_resp.get("statusMessages", {})
    percent = None
    processed = None
    total = None
    for k, v in msgs.items():
        match = re.search(r"(\d+)", str(v))
        if not match:
            continue
        num = int(match.group(1))
        lk = k.lower()
        if "percent" in lk:
            percent = num
        elif "processed" in lk:
            processed = num
        elif "total" in lk:
            total = num
    if percent is not None:
        return percent, 100, json_resp.get("status")
    return processed or 0, total, json_resp.get("status")


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
    zk_override: str | None,
    role: str,
) -> Collection:
    if zk_override:
        return _get_collection_from_zk(zk_override, collection_name)
    if context:
        return _get_collection_from_zk(context.zk, collection_name)
    for collection in cluster_state:
        if collection.name == collection_name:
            return collection
    rich.print(f"[error]❌  {role} collection {collection_name} not found!")
    raise typer.Exit(1)


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


@contextmanager
def _dataimport_progress() -> Iterator[Progress]:
    columns = (
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TimeRemainingColumn(),
    )
    with Progress(*columns) as progress:
        yield progress


async def _watch_dataimport_status(
    progress: Progress,
    task_id: int,
    base_url: str,
    dataimport_path: str,
) -> None:
    while True:
        stat = await send_request(
            base_url,
            dataimport_path,
            params={"command": "status", "wt": "json"},
        )
        done, total, status = _parse_status(stat)
        if total:
            progress.update(task_id, total=total, completed=done)
        else:
            progress.update(task_id, completed=done)
        if status != "busy":
            break
        await asyncio.sleep(1)


async def _detect_busy_shards(
    leaders: dict[str, Replica | None],
    dataimport_path: str,
) -> list[tuple[str, Replica]]:
    busy: list[tuple[str, Replica]] = []
    for name, replica in leaders.items():
        if replica is None:
            continue
        if not replica.base_url:
            rich.print(f"[error]❌  Leader for shard {name} is missing a base URL")
            raise typer.Exit(1)
        status = await send_request(
            replica.base_url,
            dataimport_path,
            params={"command": "status", "wt": "json"},
        )
        if status.get("status") == "busy":
            busy.append((name, replica))
    return busy


@app.async_command(
    help="Reindex documents from a source collection into a target collection using the dataimport handler"
)
@with_cluster_state()
async def reindex(
    cluster_state: List[Collection],
    source_collection: str = typer.Option(
        ..., "--source", help="Collection to reindex from", autocompletion=source_collection_names
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
    fq: tuple[str] | None = typer.Option(
        None,
        "--fq",
        help="Filter query to pass to the dataimport handler",
    ),
    source_shard: List[str] | None = typer.Option(
        None, "--source-shard", help="Source shards to reindex"
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
        zk_override=target_zk,
        role="Target",
    )
    source_coll = _resolve_collection(
        source_collection,
        cluster_state=cluster_state,
        context=resolved_source_context,
        zk_override=source_zk,
        role="Source",
    )

    src_shards = sorted(
        (s for s in source_coll.shards if not source_shard or s.name in source_shard),
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
    dataimport_path = f"/{target_collection}{handler}"

    busy_shards = await _detect_busy_shards(leaders, dataimport_path)
    if busy_shards:
        rich.print("[warning]⚠️  Dataimport already running on some shards. Monitoring progress...")
        with _dataimport_progress() as progress:
            tasks = {name: progress.add_task(name, total=100) for name, _ in busy_shards}
            await asyncio.gather(
                *(
                    _watch_dataimport_status(progress, tasks[name], replica.base_url, dataimport_path)
                    for name, replica in busy_shards
                )
            )
        raise typer.Exit(1)

    fq_param = ",".join(fq) if fq else None

    with _dataimport_progress() as progress:
        async def run_target(target_name: str, source_shards_for_target: List[Shard]) -> None:
            leader = leaders.get(target_name)
            if not leader or not leader.base_url:
                rich.print(f"[error]❌  No leader with a base URL found for target shard {target_name}")
                raise typer.Exit(1)
            task_id = progress.add_task(target_name, total=100)
            for shard in source_shards_for_target:
                source_replica = (
                    next((r for r in shard.replicas if r.leader), None)
                    or next((r for r in shard.replicas if r.base_url and r.core), None)
                )
                if not source_replica or not source_replica.base_url or not source_replica.core:
                    rich.print(f"[error]❌  No usable replica found for source shard {shard.name}")
                    raise typer.Exit(1)
                source_core_url = (
                    get_host_with_scheme(source_replica.base_url, "http").rstrip("/")
                    + f"/{source_replica.core}"
                )
                params = {
                    "command": "full-import",
                    "clean": "false",
                    "commit": "true",
                    "distrib": "false",
                    "wt": "json",
                    "url": source_core_url,
                    "qt": qt, 
                    "fl": fl, 
                    "timeout": timeout,
                    "rows": rows, 
                    "sort": sort
                }
                if fq_param:
                    params["fqs"] = fq_param
                await send_request(leader.base_url, dataimport_path, params=params)
                await _watch_dataimport_status(progress, task_id, leader.base_url, dataimport_path)
            progress.update(
                task_id,
                completed=progress.tasks[task_id].total
                or progress.tasks[task_id].completed,
            )

        await asyncio.gather(*(run_target(name, shards) for name, shards in shard_map.items()))

    rich.print("[success]✅  Reindex completed")
