from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import List, Literal, Sequence
from urllib.parse import quote, urlparse, urlunparse

from solradm.api.models import Collection, Replica, Shard
from solradm.api.utils import get_host_with_scheme, send_request
from solradm.config import settings


@dataclass
class SourceShardState:
    name: str
    doc_count: int | None = None
    docs_processed: int = 0
    docs_total: int | None = None
    status: Literal["pending", "running", "done", "failed"] = "pending"
    error: str | None = None


@dataclass
class TargetShardState:
    name: str
    source_shards: list[SourceShardState] = field(default_factory=list)
    current_source: str | None = None
    status: Literal["pending", "running", "done", "failed"] = "pending"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None

    @property
    def sources_done(self) -> int:
        return sum(1 for s in self.source_shards if s.status == "done")

    @property
    def sources_failed(self) -> int:
        return sum(1 for s in self.source_shards if s.status == "failed")

    @property
    def sources_total(self) -> int:
        return len(self.source_shards)


@dataclass
class ReindexConfig:
    source_collection: str
    target_collection: str
    handler: str
    fq: list[str] | None
    rows: int | None
    sort: str
    qt: str
    fl: str
    timeout: int


@dataclass
class ReindexSummary:
    total_targets: int
    completed_targets: int
    failed_targets: int
    running_targets: int
    pending_targets: int
    total_docs_processed: int
    failures: list[tuple[str, str]]


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


def _with_basic_auth(url: str) -> str:
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


async def _get_shard_doc_count(
    source_replica: Replica,
    source_collection: str,
    shard_name: str,
    fq: List[str] | None,
) -> int:
    if not source_replica.base_url or not source_replica.core:
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
        source_replica.base_url,
        f"/{source_collection}/select",
        params=params,
    )
    return resp.get("response", {}).get("numFound", 0)


class ReindexEngine:
    def __init__(
        self,
        shard_map: dict[str, List[Shard]],
        leaders: dict[str, Replica | None],
        config: ReindexConfig,
    ):
        self._config = config
        self._leaders = leaders
        self._cancelled = False
        self._done_event = asyncio.Event()
        self._dataimport_path = f"{config.handler}"

        # Build target states from shard_map
        self._target_states: list[TargetShardState] = []
        # Store source shard objects for replica lookup
        self._source_shard_objects: dict[str, Shard] = {}

        for target_name in sorted(shard_map.keys()):
            source_shards_for_target = shard_map[target_name]
            source_states = []
            for shard in source_shards_for_target:
                self._source_shard_objects[shard.name] = shard
                source_states.append(SourceShardState(name=shard.name))
            self._target_states.append(
                TargetShardState(name=target_name, source_shards=source_states)
            )

    def get_state(self) -> list[TargetShardState]:
        return self._target_states

    def get_summary(self) -> ReindexSummary:
        completed = sum(1 for t in self._target_states if t.status == "done")
        failed = sum(1 for t in self._target_states if t.status == "failed")
        running = sum(1 for t in self._target_states if t.status == "running")
        pending = sum(1 for t in self._target_states if t.status == "pending")
        total_docs = sum(
            s.docs_processed
            for t in self._target_states
            for s in t.source_shards
        )
        failures = [
            (t.name, t.error or "Unknown error")
            for t in self._target_states
            if t.status == "failed"
        ]
        return ReindexSummary(
            total_targets=len(self._target_states),
            completed_targets=completed,
            failed_targets=failed,
            running_targets=running,
            pending_targets=pending,
            total_docs_processed=total_docs,
            failures=failures,
        )

    def request_cancel(self) -> None:
        self._cancelled = True

    @property
    def is_done(self) -> bool:
        return self._done_event.is_set()

    async def run(self) -> None:
        try:
            await asyncio.gather(
                *(self._run_target(target) for target in self._target_states)
            )
        finally:
            self._done_event.set()

    async def _run_target(self, target: TargetShardState) -> None:
        if self._cancelled:
            return

        target.status = "running"
        target.started_at = time.monotonic()

        leader = self._leaders.get(target.name)
        if not leader or not leader.base_url:
            target.status = "failed"
            target.error = f"No leader with a base URL found for target shard {target.name}"
            target.completed_at = time.monotonic()
            return

        fq_param = ",".join(self._config.fq) if self._config.fq else None

        for source_state in target.source_shards:
            if self._cancelled:
                target.status = "failed"
                target.error = "Cancelled"
                target.completed_at = time.monotonic()
                return

            target.current_source = source_state.name
            source_state.status = "running"

            try:
                shard = self._source_shard_objects[source_state.name]
                source_replica = (
                    next((r for r in shard.replicas if r.leader), None)
                    or next((r for r in shard.replicas if r.base_url and r.core), None)
                )
                if not source_replica or not source_replica.base_url or not source_replica.core:
                    raise RuntimeError(f"No usable replica found for source shard {source_state.name}")

                doc_count = await _get_shard_doc_count(
                    source_replica,
                    self._config.source_collection,
                    source_state.name,
                    self._config.fq,
                )
                source_state.doc_count = doc_count

                source_core_url = (
                    get_host_with_scheme(source_replica.base_url, "http").rstrip("/")
                    + f"/{source_replica.core}"
                )
                source_core_url = _with_basic_auth(source_core_url)

                params = {
                    "command": "full-import",
                    "clean": "false",
                    "commit": "false",
                    "optimize": "false",
                    "wt": "json",
                    "url": source_core_url,
                    "qt": self._config.qt,
                    "fl": self._config.fl,
                    "timeout": self._config.timeout,
                    "sort": self._config.sort,
                }
                if self._config.rows is not None:
                    params["rows"] = self._config.rows
                if fq_param:
                    params["fqs"] = fq_param

                await send_request(leader.base_url, f'/{leader.core}{self._dataimport_path}', params=params)
                await self._poll_dataimport(source_state, leader.base_url, f'/{leader.core}{self._dataimport_path}')

                source_state.status = "done"

            except Exception as e:
                source_state.status = "failed"
                source_state.error = str(e)
                target.status = "failed"
                target.error = f"Source {source_state.name}: {e}"
                target.completed_at = time.monotonic()
                return

        target.status = "done"
        target.current_source = None
        target.completed_at = time.monotonic()

    async def _poll_dataimport(
        self, source: SourceShardState, base_url: str, path: str
    ) -> None:
        while True:
            if self._cancelled:
                return
            stat = await send_request(
                base_url,
                path,
                params={"command": "status", "wt": "json"},
            )
            done, total, status = _parse_status(stat)
            source.docs_processed = done
            source.docs_total = total
            if status != "busy":
                break
            await asyncio.sleep(self._get_poll_interval())

    def _get_poll_interval(self) -> float:
        running = sum(1 for t in self._target_states if t.status == "running")
        if running <= 100:
            return 1.0
        elif running <= 200:
            return 2.0
        else:
            return 3.0
