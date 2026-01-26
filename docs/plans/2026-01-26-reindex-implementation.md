# Reindex Command Rewrite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite the reindex command with a Textual UI that handles 400+ shards efficiently.

**Architecture:** Three-layer design: CLI entry point (Typer) → Textual UI (ReindexApp) → ReindexEngine (async state management). All target shards run concurrently; source shards within each target are sequential.

**Tech Stack:** Python 3.13, Textual, asyncio, aiohttp, Typer, Pydantic

**Working Directory:** `/home/maizepound5/repos/solradm/.worktrees/reindex-textual`

---

## Task 1: Add Textual Dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add textual to dependencies**

Edit `pyproject.toml` to add textual to the dependencies list:

```toml
dependencies = [
    "aiohttp==3.12.15",
    "async-typer>=0.1.10",
    "debugpy>=1.8.0",
    "dynaconf>=3.2.11",
    "kazoo>=2.10.0",
    "kubernetes>=33.1.0",
    "platformdirs>=4.3.8",
    "pydantic>=2.11.7",
    "pytest>=8.4.1",
    "pyyaml>=6.0.2",
    "textual>=0.50.0",
    "typer>=0.20.0",
    "watchdog>=6.0.0",
]
```

**Step 2: Install updated dependencies**

Run: `uv pip install -e /home/maizepound5/repos/solradm/.worktrees/reindex-textual --python /home/maizepound5/repos/solradm/.venv/bin/python`

Expected: Textual installed successfully

**Step 3: Verify textual import works**

Run: `/home/maizepound5/repos/solradm/.venv/bin/python -c "import textual; print(textual.__version__)"`

Expected: Version number printed (0.50.0 or higher)

**Step 4: Commit**

```bash
cd /home/maizepound5/repos/solradm/.worktrees/reindex-textual
git add pyproject.toml
git commit -m "deps: add textual for reindex UI"
```

---

## Task 2: Create Data Structures Module

**Files:**
- Create: `src/solradm/commands/collections/reindex_types.py`
- Test: `tests/test_reindex_types.py`

**Step 1: Write the test file**

Create `tests/test_reindex_types.py`:

```python
from solradm.commands.collections.reindex_types import (
    SourceShardState,
    TargetShardState,
    ReindexConfig,
)


def test_source_shard_state_defaults():
    state = SourceShardState(name="shard_001")
    assert state.name == "shard_001"
    assert state.status == "pending"
    assert state.doc_count is None
    assert state.docs_processed == 0
    assert state.error is None


def test_target_shard_state_defaults():
    source = SourceShardState(name="src_001")
    state = TargetShardState(name="tgt_001", source_shards=[source])
    assert state.name == "tgt_001"
    assert state.status == "pending"
    assert state.current_source is None
    assert len(state.source_shards) == 1


def test_target_shard_state_progress_calculation():
    sources = [
        SourceShardState(name="src_001", doc_count=100, docs_processed=100, status="done"),
        SourceShardState(name="src_002", doc_count=200, docs_processed=50, status="running"),
        SourceShardState(name="src_003", doc_count=300, status="pending"),
    ]
    state = TargetShardState(name="tgt_001", source_shards=sources)
    # Total: 600 docs, processed: 150
    assert state.total_docs == 600
    assert state.docs_processed == 150


def test_reindex_config():
    config = ReindexConfig(
        target_collection="target_coll",
        source_collection="source_coll",
        handler="/dataimport",
        rows=2000,
    )
    assert config.target_collection == "target_coll"
    assert config.handler == "/dataimport"
```

**Step 2: Run test to verify it fails**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_types.py -v`

Expected: FAIL with "ModuleNotFoundError: No module named 'solradm.commands.collections.reindex_types'"

**Step 3: Write the implementation**

Create `src/solradm/commands/collections/reindex_types.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SourceShardState:
    """State for a single source shard being reindexed."""
    name: str
    doc_count: int | None = None
    docs_processed: int = 0
    status: Literal["pending", "running", "done", "failed"] = "pending"
    error: str | None = None


@dataclass
class TargetShardState:
    """State for a target shard and all its source shards."""
    name: str
    source_shards: list[SourceShardState] = field(default_factory=list)
    current_source: str | None = None
    status: Literal["pending", "running", "done", "failed"] = "pending"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None

    @property
    def total_docs(self) -> int:
        """Total documents across all source shards."""
        return sum(s.doc_count or 0 for s in self.source_shards)

    @property
    def docs_processed(self) -> int:
        """Total documents processed across all source shards."""
        return sum(s.docs_processed for s in self.source_shards)


@dataclass
class ReindexConfig:
    """Configuration for a reindex operation."""
    target_collection: str
    source_collection: str
    handler: str = "/dataimport"
    rows: int = 2000
    sort: str = "first_timestamp asc, item_id asc"
    qt: str = "/dih"
    fl: str = "*,ignored_tmp1:_version_"
    timeout: int = 300
    fq: list[str] | None = None
```

**Step 4: Run test to verify it passes**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_types.py -v`

Expected: All 4 tests PASS

**Step 5: Commit**

```bash
cd /home/maizepound5/repos/solradm/.worktrees/reindex-textual
git add src/solradm/commands/collections/reindex_types.py tests/test_reindex_types.py
git commit -m "feat(reindex): add data structure types for reindex state"
```

---

## Task 3: Create ReindexEngine Core

**Files:**
- Create: `src/solradm/commands/collections/reindex_engine.py`
- Test: `tests/test_reindex_engine.py`

**Step 1: Write the test file**

Create `tests/test_reindex_engine.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from solradm.commands.collections.reindex_engine import ReindexEngine
from solradm.commands.collections.reindex_types import ReindexConfig, SourceShardState


@pytest.fixture
def mock_leaders():
    """Mock leader replicas for target shards."""
    from solradm.api.models import Replica
    return {
        "shard1": Replica(
            name="core_node1",
            core="target_shard1_replica1",
            node_name="node1:8983_solr",
            type="NRT",
            state="active",
            leader=True,
            force_set_state=False,
            base_url="http://node1:8983/solr",
        ),
        "shard2": Replica(
            name="core_node2",
            core="target_shard2_replica1",
            node_name="node2:8983_solr",
            type="NRT",
            state="active",
            leader=True,
            force_set_state=False,
            base_url="http://node2:8983/solr",
        ),
    }


@pytest.fixture
def mock_shard_map():
    """Mock source-to-target shard mapping."""
    from solradm.api.models import Shard, Replica

    def make_shard(name: str) -> Shard:
        replica = Replica(
            name=f"{name}_replica",
            core=f"{name}_core",
            node_name="srcnode:8983_solr",
            type="NRT",
            state="active",
            leader=True,
            force_set_state=False,
            base_url="http://srcnode:8983/solr",
        )
        return Shard(name=name, range="80000000-ffffffff", replicas=[replica])

    return {
        "shard1": [make_shard("src_001"), make_shard("src_002")],
        "shard2": [make_shard("src_003")],
    }


@pytest.fixture
def config():
    return ReindexConfig(
        target_collection="target_coll",
        source_collection="source_coll",
    )


def test_engine_initialization(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)
    state = engine.get_state()

    assert len(state) == 2
    assert state[0].name == "shard1"
    assert len(state[0].source_shards) == 2
    assert state[1].name == "shard2"
    assert len(state[1].source_shards) == 1


def test_engine_get_summary(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)
    summary = engine.get_summary()

    assert summary["total"] == 2
    assert summary["pending"] == 2
    assert summary["running"] == 0
    assert summary["done"] == 0
    assert summary["failed"] == 0


def test_adaptive_poll_interval(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)

    # With 0 running, should be 1 second
    assert engine._get_poll_interval() == 1.0

    # Simulate running state
    engine._target_states[0].status = "running"
    assert engine._get_poll_interval() == 1.0


@pytest.mark.asyncio
async def test_engine_run_success(mock_shard_map, mock_leaders, config):
    engine = ReindexEngine(mock_shard_map, mock_leaders, config)

    # Mock the async methods
    with patch.object(engine, '_get_shard_doc_count', new_callable=AsyncMock) as mock_count, \
         patch.object(engine, '_start_dataimport', new_callable=AsyncMock) as mock_start, \
         patch.object(engine, '_poll_dataimport_status', new_callable=AsyncMock) as mock_poll:

        mock_count.return_value = 1000
        mock_poll.return_value = ("done", 1000)

        await engine.run()

        summary = engine.get_summary()
        assert summary["done"] == 2
        assert summary["failed"] == 0
```

**Step 2: Run test to verify it fails**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_engine.py -v`

Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write the implementation**

Create `src/solradm/commands/collections/reindex_engine.py`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_engine.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
cd /home/maizepound5/repos/solradm/.worktrees/reindex-textual
git add src/solradm/commands/collections/reindex_engine.py tests/test_reindex_engine.py
git commit -m "feat(reindex): add ReindexEngine for async state management"
```

---

## Task 4: Create Textual UI - SummaryBar Widget

**Files:**
- Create: `src/solradm/commands/collections/reindex_ui.py`
- Test: `tests/test_reindex_ui.py`

**Step 1: Write the test file**

Create `tests/test_reindex_ui.py`:

```python
import pytest
from textual.widgets import Static

from solradm.commands.collections.reindex_ui import SummaryBar


def test_summary_bar_format_docs():
    bar = SummaryBar()
    assert bar._format_docs(0) == "0"
    assert bar._format_docs(999) == "999"
    assert bar._format_docs(1000) == "1.0K"
    assert bar._format_docs(1500) == "1.5K"
    assert bar._format_docs(1000000) == "1.0M"
    assert bar._format_docs(1234567) == "1.2M"


def test_summary_bar_format_time():
    bar = SummaryBar()
    assert bar._format_time(0) == "0s"
    assert bar._format_time(45) == "45s"
    assert bar._format_time(60) == "1m 0s"
    assert bar._format_time(90) == "1m 30s"
    assert bar._format_time(3661) == "1h 1m"
```

**Step 2: Run test to verify it fails**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_ui.py -v`

Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write the SummaryBar implementation**

Create `src/solradm/commands/collections/reindex_ui.py`:

```python
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static
from textual.containers import Container

from solradm.commands.collections.reindex_types import TargetShardState


class SummaryBar(Static):
    """Displays aggregate progress statistics."""

    DEFAULT_CSS = """
    SummaryBar {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._total = 0
        self._running = 0
        self._done = 0
        self._failed = 0
        self._docs_processed = 0
        self._total_docs = 0

    def _format_docs(self, count: int) -> str:
        """Format document count with K/M suffix."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _format_time(self, seconds: float) -> str:
        """Format seconds as human-readable time."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins}m {secs}s"
        else:
            hours = seconds // 3600
            mins = (seconds % 3600) // 60
            return f"{hours}h {mins}m"

    def update_stats(
        self,
        total: int,
        running: int,
        done: int,
        failed: int,
        docs_processed: int,
        total_docs: int,
    ) -> None:
        """Update summary statistics."""
        self._total = total
        self._running = running
        self._done = done
        self._failed = failed
        self._docs_processed = docs_processed
        self._total_docs = total_docs
        self._update_display()

    def _update_display(self) -> None:
        """Render the summary bar."""
        pct = (
            int(100 * self._docs_processed / self._total_docs)
            if self._total_docs > 0
            else 0
        )
        parts = [
            f"Running: {self._running}/{self._total}",
            f"Completed: {self._done}",
            f"Failed: {self._failed}",
            f"Progress: {pct}% ({self._format_docs(self._docs_processed)})",
        ]
        self.update(" | ".join(parts))
```

**Step 4: Run test to verify it passes**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_ui.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
cd /home/maizepound5/repos/solradm/.worktrees/reindex-textual
git add src/solradm/commands/collections/reindex_ui.py tests/test_reindex_ui.py
git commit -m "feat(reindex): add SummaryBar widget for progress display"
```

---

## Task 5: Create Textual UI - ReindexApp

**Files:**
- Modify: `src/solradm/commands/collections/reindex_ui.py`
- Test: `tests/test_reindex_ui.py`

**Step 1: Add test for ReindexApp**

Append to `tests/test_reindex_ui.py`:

```python
from unittest.mock import MagicMock, AsyncMock

from solradm.commands.collections.reindex_types import SourceShardState, TargetShardState


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.get_state.return_value = [
        TargetShardState(
            name="shard_001",
            source_shards=[SourceShardState(name="src_001", doc_count=1000, docs_processed=500, status="running")],
            status="running",
            current_source="src_001",
        ),
        TargetShardState(
            name="shard_002",
            source_shards=[SourceShardState(name="src_002", doc_count=2000, docs_processed=2000, status="done")],
            status="done",
        ),
    ]
    engine.get_summary.return_value = {
        "total": 2,
        "running": 1,
        "done": 1,
        "failed": 0,
        "pending": 0,
        "docs_processed": 2500,
        "total_docs": 3000,
    }
    engine.run = AsyncMock()
    return engine


def test_reindex_app_sort_key():
    from solradm.commands.collections.reindex_ui import ReindexApp

    # Status priority: running=0, pending=1, failed=2, done=3
    running = TargetShardState(name="b_shard", source_shards=[], status="running")
    pending = TargetShardState(name="a_shard", source_shards=[], status="pending")
    failed = TargetShardState(name="c_shard", source_shards=[], status="failed")
    done = TargetShardState(name="d_shard", source_shards=[], status="done")

    states = [done, pending, failed, running]
    sorted_states = sorted(states, key=ReindexApp._sort_key)

    assert sorted_states[0].status == "running"
    assert sorted_states[1].status == "pending"
    assert sorted_states[2].status == "failed"
    assert sorted_states[3].status == "done"
```

**Step 2: Run test to verify it fails**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_ui.py::test_reindex_app_sort_key -v`

Expected: FAIL with "cannot import name 'ReindexApp'"

**Step 3: Add ReindexApp implementation**

Append to `src/solradm/commands/collections/reindex_ui.py`:

```python
import asyncio
import time


STATUS_PRIORITY = {"running": 0, "pending": 1, "failed": 2, "done": 3}


class ReindexApp(App):
    """Textual app for reindex progress display."""

    TITLE = "Solr Reindex"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    Screen {
        background: $surface;
    }

    DataTable {
        height: 1fr;
    }

    DataTable > .datatable--cursor {
        background: $accent;
    }

    .status-running {
        color: $warning;
    }

    .status-done {
        color: $success;
    }

    .status-failed {
        color: $error;
    }

    .status-pending {
        color: $text-muted;
    }
    """

    def __init__(self, engine, **kwargs):
        super().__init__(**kwargs)
        self._engine = engine
        self._table: DataTable | None = None
        self._summary_bar: SummaryBar | None = None
        self._row_keys: dict[str, str] = {}  # target_name -> row_key
        self._start_time = time.time()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar(id="summary")
        yield DataTable(id="shards")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize table and start background tasks."""
        self._summary_bar = self.query_one("#summary", SummaryBar)
        self._table = self.query_one("#shards", DataTable)

        # Set up columns
        self._table.add_column("Target Shard", key="target")
        self._table.add_column("Status", key="status")
        self._table.add_column("Current Source", key="source")
        self._table.add_column("Progress", key="progress")
        self._table.add_column("Elapsed", key="elapsed")
        self._table.add_column("Error", key="error")

        # Add initial rows
        for target in self._engine.get_state():
            row_key = self._table.add_row(
                target.name,
                target.status,
                target.current_source or "-",
                self._format_progress(target),
                "-",
                target.error or "",
                key=target.name,
            )
            self._row_keys[target.name] = row_key

        # Start background tasks
        asyncio.create_task(self._run_engine())
        self.set_interval(0.2, self._refresh_display)

    @staticmethod
    def _sort_key(target: TargetShardState) -> tuple[int, str]:
        """Sort key: status priority, then name."""
        return (STATUS_PRIORITY.get(target.status, 99), target.name)

    def _format_progress(self, target: TargetShardState) -> str:
        """Format progress string for a target shard."""
        if target.status == "pending":
            return "-"

        # Find current source or sum all
        total = target.total_docs
        processed = target.docs_processed

        if total == 0:
            return "-"

        pct = int(100 * processed / total) if total > 0 else 0
        return f"{pct}% ({self._format_docs(processed)}/{self._format_docs(total)})"

    def _format_docs(self, count: int) -> str:
        """Format document count with K/M suffix."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _format_elapsed(self, target: TargetShardState) -> str:
        """Format elapsed time for a target shard."""
        if target.started_at is None:
            return "-"

        end_time = target.completed_at or time.time()
        elapsed = end_time - target.started_at

        seconds = int(elapsed)
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins}m {secs}s"
        else:
            hours = seconds // 3600
            mins = (seconds % 3600) // 60
            return f"{hours}h {mins}m"

    def _refresh_display(self) -> None:
        """Update table and summary bar with current state."""
        if not self._table or not self._summary_bar:
            return

        # Update summary
        summary = self._engine.get_summary()
        self._summary_bar.update_stats(
            total=summary["total"],
            running=summary["running"],
            done=summary["done"],
            failed=summary["failed"],
            docs_processed=summary["docs_processed"],
            total_docs=summary["total_docs"],
        )

        # Update table rows
        states = sorted(self._engine.get_state(), key=self._sort_key)

        # Clear and rebuild table to maintain sort order
        self._table.clear()
        for target in states:
            self._table.add_row(
                target.name,
                target.status,
                target.current_source or "-",
                self._format_progress(target),
                self._format_elapsed(target),
                target.error or "",
                key=target.name,
            )

    async def _run_engine(self) -> None:
        """Run the reindex engine in background."""
        try:
            await self._engine.run()
        except Exception as e:
            self.notify(f"Engine error: {e}", severity="error")
        finally:
            # Final refresh
            self._refresh_display()

    def action_quit(self) -> None:
        """Handle quit action."""
        self._engine.request_cancel()
        self.exit()
```

**Step 4: Run test to verify it passes**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_ui.py -v`

Expected: All tests PASS

**Step 5: Commit**

```bash
cd /home/maizepound5/repos/solradm/.worktrees/reindex-textual
git add src/solradm/commands/collections/reindex_ui.py tests/test_reindex_ui.py
git commit -m "feat(reindex): add ReindexApp Textual application"
```

---

## Task 6: Rewrite CLI Entry Point

**Files:**
- Modify: `src/solradm/commands/collections/reindex.py`

**Step 1: Read current reindex.py for reference**

The file is already read. We'll preserve: `_resolve_collection`, `_map_source_to_targets`, `_leaders_by_shard`, `_get_collection_from_zk`, `_detect_busy_shards`, and CLI argument definitions.

**Step 2: Rewrite reindex.py**

Replace entire contents of `src/solradm/commands/collections/reindex.py`:

```python
from __future__ import annotations

import asyncio
import json
from typing import List, Sequence

import rich
import typer
from kazoo.client import KazooClient

from solradm.api.models import Collection, Replica, Shard
from solradm.api.utils import send_request
from solradm.commands.collections.reindex_engine import ReindexEngine
from solradm.commands.collections.reindex_types import ReindexConfig
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
    """Fetch collection state directly from ZooKeeper."""
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
) -> Collection | None:
    """Resolve collection from ZK or cluster state."""
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
    """Map source shards to target shards using round-robin distribution."""
    shard_map: dict[str, List[Shard]] = {}
    for idx, shard in enumerate(source_shards):
        target_name = target_shards[idx % len(target_shards)].name
        shard_map.setdefault(target_name, []).append(shard)
    return shard_map


def _leaders_by_shard(shards: Sequence[Shard]) -> dict[str, Replica | None]:
    """Get leader replica for each shard."""
    return {
        shard.name: next((r for r in shard.replicas if r.leader), None)
        for shard in shards
    }


async def _detect_busy_shards(
    leaders: dict[str, Replica | None],
    dataimport_path: str,
) -> list[tuple[str, Replica]]:
    """Check for shards with dataimport already running."""
    busy: list[tuple[str, Replica]] = []
    for name, replica in leaders.items():
        if replica is None:
            continue
        if not replica.base_url:
            continue
        try:
            status = await send_request(
                replica.base_url,
                dataimport_path,
                params={"command": "status", "wt": "json"},
            )
            if status.get("status") == "busy":
                busy.append((name, replica))
        except Exception:
            pass  # Skip unreachable shards
    return busy


def _print_final_summary(engine: ReindexEngine) -> None:
    """Print final summary after UI exits."""
    summary = engine.get_summary()
    state = engine.get_state()

    rich.print("\n[bold]Reindex Summary:[/bold]")
    rich.print(f"  Completed: {summary['done']} targets ({summary['docs_processed']:,} documents)")

    if summary["failed"] > 0:
        rich.print(f"  [error]Failed: {summary['failed']} targets[/error]")
        for target in state:
            if target.status == "failed":
                error_msg = target.error or "Unknown error"
                # Find first failed source for more detail
                for source in target.source_shards:
                    if source.status == "failed" and source.error:
                        error_msg = f"{source.error} on source {source.name}"
                        break
                rich.print(f"    - {target.name}: {error_msg}")


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
    sort: str = typer.Option("first_timestamp asc, item_id asc", help="Sort criteria for the cursorMark requests from the source collection."),
    qt: str = typer.Option("/dih", help="Request handler to fetch from the source collection."),
    fl: str = typer.Option("*,ignored_tmp1:_version_", help="Fields to reindex. By default, reindexes all fields."),
    timeout: int = typer.Option(300, help="The query timeout from the source collection, in seconds."),
) -> None:
    # Validate mutual exclusivity
    if (source_context and source_zk) or (target_context and target_zk):
        rich.print("[error]❌  Context and ZooKeeper overrides are mutually exclusive")
        raise typer.Exit(1)

    if all_shards and shards:
        rich.print("[error]❌  --shards and --all are mutually exclusive")
        raise typer.Exit(1)

    if not all_shards and not shards:
        rich.print("[error]❌  Either --shards or --all must be provided")
        raise typer.Exit(1)

    # Resolve contexts
    resolved_source_context = None
    if source_context:
        resolved_source_context = get_context(source_context)
        if not resolved_source_context:
            rich.print(f"[error]❌  Source context {source_context} not found")
            raise typer.Exit(1)

    resolved_target_context = None
    if target_context:
        resolved_target_context = get_context(target_context)
        if not resolved_target_context:
            rich.print(f"[error]❌  Target context {target_context} not found")
            raise typer.Exit(1)

    # Resolve collections
    target_coll = _resolve_collection(
        target_collection,
        cluster_state=cluster_state,
        context=resolved_target_context,
        zk_override=target_zk,
    )
    if not target_coll:
        rich.print(f"[error]❌  Target collection {target_collection} not found!")
        raise typer.Exit(1)

    source_coll = _resolve_collection(
        source_collection,
        cluster_state=cluster_state,
        context=resolved_source_context,
        zk_override=source_zk,
    )
    if not source_coll:
        rich.print(f"[error]❌  Source collection {source_collection} not found!")
        raise typer.Exit(1)

    # Filter source shards
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

    # Build shard mapping and get leaders
    shard_map = _map_source_to_targets(src_shards, tgt_shards)
    leaders = _leaders_by_shard(tgt_shards)
    dataimport_path = f"/{target_collection}{handler}"

    # Check for busy shards
    busy_shards = await _detect_busy_shards(leaders, dataimport_path)
    if busy_shards:
        rich.print("[warning]⚠️  Dataimport already running on some shards:")
        for name, _ in busy_shards:
            rich.print(f"    - {name}")
        rich.print("[error]❌  Please wait for existing operations to complete")
        raise typer.Exit(1)

    # Create config and engine
    config = ReindexConfig(
        target_collection=target_collection,
        source_collection=source_collection,
        handler=handler,
        rows=rows,
        sort=sort,
        qt=qt,
        fl=fl,
        timeout=timeout,
        fq=list(fq) if fq else None,
    )
    engine = ReindexEngine(shard_map, leaders, config)

    # Launch Textual app
    ui = ReindexApp(engine)
    await ui.run_async()

    # Print final summary
    _print_final_summary(engine)

    # Exit with error if any failures
    summary = engine.get_summary()
    if summary["failed"] > 0:
        raise typer.Exit(1)

    rich.print("[success]✅  Reindex completed")
```

**Step 3: Verify CLI loads**

Run: `/home/maizepound5/repos/solradm/.venv/bin/python -c "from solradm.commands.collections.reindex import reindex; print('OK')"`

Expected: "OK"

**Step 4: Commit**

```bash
cd /home/maizepound5/repos/solradm/.worktrees/reindex-textual
git add src/solradm/commands/collections/reindex.py
git commit -m "feat(reindex): rewrite CLI entry point with Textual UI integration"
```

---

## Task 7: Integration Test

**Files:**
- Run CLI to verify end-to-end

**Step 1: Verify CLI help works**

Run: `/home/maizepound5/repos/solradm/.venv/bin/sa coll reindex --help`

Expected: Help output showing all options

**Step 2: Verify sa command loads without errors**

Run: `/home/maizepound5/repos/solradm/.venv/bin/sa --help`

Expected: Main help output without import errors

**Step 3: Run existing tests**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/test_reindex_types.py tests/test_reindex_engine.py tests/test_reindex_ui.py -v`

Expected: All tests pass

**Step 4: Commit any fixes if needed**

If tests revealed issues, fix and commit.

---

## Task 8: Final Cleanup and Documentation

**Files:**
- Remove unused code from old implementation

**Step 1: Verify no unused imports in reindex.py**

Check that all imports in reindex.py are used.

**Step 2: Run full test suite**

Run: `/home/maizepound5/repos/solradm/.venv/bin/pytest tests/ -v --ignore=tests/test_kube* --ignore=tests/test_context_repositories.py`

Expected: All non-integration tests pass

**Step 3: Final commit**

```bash
cd /home/maizepound5/repos/solradm/.worktrees/reindex-textual
git add -A
git commit -m "chore(reindex): cleanup and finalize rewrite"
```

---

## Summary

The implementation creates three new files:
1. `reindex_types.py` - Data structures for state management
2. `reindex_engine.py` - Async engine managing all operations
3. `reindex_ui.py` - Textual app with scrollable UI

And rewrites `reindex.py` to integrate them while preserving the CLI interface.

Key improvements:
- All target shards run concurrently via `asyncio.gather()`
- Source shards are sequential per target (dataimport limitation)
- Adaptive polling reduces Solr load at scale
- Scrollable DataTable handles 400+ shards
- Continue-on-failure with final summary
- Graceful shutdown on 'q' press
