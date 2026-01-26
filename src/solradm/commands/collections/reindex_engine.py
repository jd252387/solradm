from __future__ import annotations

import asyncio
import re
import time
from typing import List, Sequence
from urllib.parse import quote, urlparse, urlunparse

from solradm.api.models import Replica, Shard
from solradm.api.utils import get_host_with_scheme, send_request
from solradm.commands.collections.reindex_types import (
    ReindexConfig,
    SourceShardState,
    TargetShardState,
)
from solradm.config import settings


def _parse_status(json_resp: dict) -> tuple[int, int | None, str | None]:
    """Parse dataimport status response to extract progress info."""
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


def _with_basic_auth(url: str) -> str:
    """Add basic auth credentials to URL if configured."""
    auth = settings.get("auth")
    if not auth:
        return url

    user = settings.auth.user
    password = settings.auth.password

    if user is None or password is None:
        return url

    parsed = urlparse(url)
    if not parsed.hostname:
        return url

    encoded_user = quote(str(user), safe="")
    encoded_password = quote(str(password), safe="")
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    netloc = f"{encoded_user}:{encoded_password}@{netloc}"

    return urlunparse(parsed._replace(netloc=netloc))


class ReindexEngine:
    """Manages reindex operations independent of UI."""

    def __init__(
        self,
        shard_map: dict[str, List[Shard]],
        leaders: dict[str, Replica | None],
        config: ReindexConfig,
    ):
        self._shard_map = shard_map
        self._leaders = leaders
        self._config = config
        self._cancelled = False
        self._target_states: list[TargetShardState] = []

        # Initialize state for each target shard
        for target_name, source_shards in sorted(shard_map.items()):
            source_states = [
                SourceShardState(name=s.name) for s in source_shards
            ]
            self._target_states.append(
                TargetShardState(name=target_name, source_shards=source_states)
            )

    def get_state(self) -> list[TargetShardState]:
        """Return current state snapshot for UI."""
        return self._target_states

    def get_summary(self) -> dict:
        """Return aggregate statistics."""
        total = len(self._target_states)
        pending = sum(1 for t in self._target_states if t.status == "pending")
        running = sum(1 for t in self._target_states if t.status == "running")
        done = sum(1 for t in self._target_states if t.status == "done")
        failed = sum(1 for t in self._target_states if t.status == "failed")
        total_docs = sum(t.total_docs for t in self._target_states)
        docs_processed = sum(t.docs_processed for t in self._target_states)

        return {
            "total": total,
            "pending": pending,
            "running": running,
            "done": done,
            "failed": failed,
            "total_docs": total_docs,
            "docs_processed": docs_processed,
        }

    def request_cancel(self) -> None:
        """Signal graceful shutdown."""
        self._cancelled = True

    def _get_poll_interval(self) -> float:
        """Return adaptive poll interval based on running count."""
        running = sum(1 for t in self._target_states if t.status == "running")
        if running > 200:
            return 3.0
        elif running > 100:
            return 2.0
        return 1.0

    async def _get_shard_doc_count(
        self,
        source_shard: Shard,
        fq: list[str] | None,
    ) -> int:
        """Query source shard to get document count."""
        replica = next(
            (r for r in source_shard.replicas if r.leader),
            next((r for r in source_shard.replicas if r.base_url and r.core), None),
        )
        if not replica or not replica.base_url or not replica.core:
            return 0

        params = {
            "q": "*:*",
            "rows": "0",
            "wt": "json",
            "distrib": "false",
        }
        if fq:
            params["fq"] = fq

        resp = await send_request(
            replica.base_url,
            f"/{self._config.source_collection}/select",
            params=params,
        )
        return resp.get("response", {}).get("numFound", 0)

    async def _start_dataimport(
        self,
        leader: Replica,
        source_shard: Shard,
    ) -> None:
        """Start a dataimport full-import on target shard."""
        replica = next(
            (r for r in source_shard.replicas if r.leader),
            next((r for r in source_shard.replicas if r.base_url and r.core), None),
        )
        if not replica or not replica.base_url or not replica.core:
            raise ValueError(f"No usable replica for source shard {source_shard.name}")

        source_core_url = (
            get_host_with_scheme(replica.base_url, "http").rstrip("/")
            + f"/{replica.core}"
        )
        source_core_url = _with_basic_auth(source_core_url)

        params = {
            "command": "full-import",
            "clean": "false",
            "commit": "true",
            "distrib": "false",
            "wt": "json",
            "url": source_core_url,
            "qt": self._config.qt,
            "fl": self._config.fl,
            "timeout": self._config.timeout,
            "rows": self._config.rows,
            "sort": self._config.sort,
        }
        if self._config.fq:
            params["fqs"] = ",".join(self._config.fq)

        dataimport_path = f"/{self._config.target_collection}{self._config.handler}"
        await send_request(leader.base_url, dataimport_path, params=params)

    async def _poll_dataimport_status(
        self,
        leader: Replica,
    ) -> tuple[str, int]:
        """Poll dataimport status until complete. Returns (status, docs_processed)."""
        dataimport_path = f"/{self._config.target_collection}{self._config.handler}"

        while not self._cancelled:
            stat = await send_request(
                leader.base_url,
                dataimport_path,
                params={"command": "status", "wt": "json"},
            )
            done, total, status = _parse_status(stat)

            if status != "busy":
                return status or "done", done

            await asyncio.sleep(self._get_poll_interval())

        return "cancelled", 0

    async def _run_target(self, target_state: TargetShardState) -> None:
        """Process all source shards for a single target shard."""
        leader = self._leaders.get(target_state.name)
        if not leader or not leader.base_url:
            target_state.status = "failed"
            target_state.error = f"No leader found for target shard {target_state.name}"
            return

        target_state.status = "running"
        target_state.started_at = time.time()

        source_shards = self._shard_map[target_state.name]
        all_failed = True

        for i, source_shard in enumerate(source_shards):
            if self._cancelled:
                break

            source_state = target_state.source_shards[i]
            target_state.current_source = source_state.name
            source_state.status = "running"

            try:
                # Get doc count
                doc_count = await self._get_shard_doc_count(
                    source_shard, self._config.fq
                )
                source_state.doc_count = doc_count

                # Start dataimport
                await self._start_dataimport(leader, source_shard)

                # Poll until complete
                status, docs_processed = await self._poll_dataimport_status(leader)
                source_state.docs_processed = docs_processed

                if status in ("done", "idle"):
                    source_state.status = "done"
                    all_failed = False
                else:
                    source_state.status = "failed"
                    source_state.error = f"Dataimport status: {status}"

            except Exception as e:
                source_state.status = "failed"
                source_state.error = str(e)

        target_state.current_source = None
        target_state.completed_at = time.time()

        if all_failed and target_state.source_shards:
            target_state.status = "failed"
            target_state.error = "All source shards failed"
        else:
            target_state.status = "done"

    async def run(self) -> None:
        """Run all reindex operations."""
        await asyncio.gather(
            *[self._run_target(target) for target in self._target_states]
        )
