# Reindex Command Rewrite Design

## Problem Statement

The current reindex command has two issues at scale (400+ shards):

1. **Performance bugs**: The progress tracking and request management don't efficiently handle large numbers of concurrent operations
2. **UI limitations**: Rich progress bars become unreadable and crash the terminal with many shards

## Requirements

- All target shards reindex concurrently
- Source shards within each target are sequential (dataimport limitation)
- All target shards stay busy at all times
- Scrollable UI to handle 400+ shards
- Continue on failure, report all failures at end
- Adaptive polling to reduce Solr load under high concurrency

## Architecture

Three-layer separation of concerns:

```
┌─────────────────────────────────────────────────────────┐
│                    CLI Entry Point                       │
│            (Typer command, argument parsing)             │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│                   Textual UI                             │
│         (ReindexApp - rendering, user input)             │
│                      │                                   │
│              reads state via                             │
│              engine.get_state()                          │
│                      │                                   │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│                 ReindexEngine                            │
│    (async tasks, state management, Solr operations)      │
└─────────────────────────────────────────────────────────┘
```

## Data Structures

```python
@dataclass
class SourceShardState:
    name: str
    doc_count: int | None = None
    docs_processed: int = 0
    status: Literal["pending", "running", "done", "failed"] = "pending"
    error: str | None = None

@dataclass
class TargetShardState:
    name: str
    source_shards: list[SourceShardState]
    current_source: str | None = None
    status: Literal["pending", "running", "done", "failed"] = "pending"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
```

## ReindexEngine

### Responsibilities

- Manages all reindexing operations independent of UI
- Maintains state for each target shard
- Exposes state via `get_state()` for UI to read
- Each target shard runs as an independent asyncio task

### Core Methods

- `__init__(shard_map, leaders, config)` - Initialize with source→target mapping
- `async run()` - Launch one task per target via `asyncio.gather()`
- `get_state() -> list[TargetShardState]` - Return current state snapshot
- `get_summary() -> dict` - Return aggregate stats
- `request_cancel()` - Signal graceful shutdown

### Per-Target Task Flow

1. Mark target as "running"
2. For each source shard (sequential):
   - Fetch doc count, mark source as "running"
   - Send full-import request
   - Poll status with adaptive interval until complete
   - Mark source as "done" or "failed"
3. Mark target as "done" (or "failed" if all sources failed)

### Adaptive Polling

| Running Count | Poll Interval |
|---------------|---------------|
| 1-100         | 1 second      |
| 101-200       | 2 seconds     |
| 201+          | 3 seconds     |

## Textual UI

### App Structure

```python
class ReindexApp(App):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "filter", "Filter"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar()       # Custom widget
        yield DataTable()        # Scrollable shard table
        yield Footer()
```

### SummaryBar Widget

Single line display:
```
Running: 47/400 | Completed: 312 | Failed: 2 | Progress: 82% (15,234,567 docs)
```

### DataTable Columns

| Target Shard | Status | Current Source | Progress | Elapsed | Error |
|--------------|--------|----------------|----------|---------|-------|
| shard_001 | Running | src_042 | 45% (12K/27K) | 2m 34s | |
| shard_002 | Running | src_018 | 78% (8K/10K) | 1m 12s | |
| shard_003 | Failed | src_007 | - | 45s | Connection timeout |
| shard_004 | Done | - | 100% (156K) | 5m 22s | |

### Sorting

1. Running (sorted by name)
2. Pending (sorted by name)
3. Failed (sorted by name)
4. Done (sorted by name)

### Refresh Cycle

- Timer interval: 200ms
- Only update changed rows, not full table rebuild

### Graceful Shutdown

Pressing `q`:
1. Sets cancellation flag on engine
2. Waits for current dataimport operations to finish polling
3. Exits cleanly

## File Structure

```
src/solradm/commands/collections/
├── reindex.py              # CLI entry point (Typer command)
├── reindex_engine.py       # ReindexEngine class
└── reindex_ui.py           # Textual app (ReindexApp)
```

## CLI Entry Point

```python
@app.async_command()
@with_cluster_state()
async def reindex(cluster_state, source_collection, target_collection, ...):
    # 1. Validate inputs (same as current)
    # 2. Resolve collections from ZK/cluster_state
    # 3. Filter source shards, build shard_map
    # 4. Create engine
    engine = ReindexEngine(shard_map, leaders, config)

    # 5. Launch Textual app (blocks until complete)
    app = ReindexApp(engine)
    await app.run_async()

    # 6. Print final summary to stdout
    summary = engine.get_summary()
    if summary["failed"] > 0:
        rich.print(f"[error]❌ {summary['failed']} shards failed")
        raise typer.Exit(1)
    rich.print("[success]✅ Reindex completed")
```

## Error Handling

| Scenario | Handling |
|----------|----------|
| Source shard has no usable replica | Mark source as failed, continue to next |
| Dataimport request fails | Mark source as failed, continue to next |
| Dataimport returns error status | Mark source as failed, continue to next |
| Target leader unavailable | Mark entire target as failed |
| All sources for a target fail | Mark target as failed |
| User presses `q` | Graceful shutdown after current operations |

## Final Summary Output

After UI exits, print to stdout:
```
Reindex Summary:
  Completed: 398 targets (1,234,567 documents)
  Failed: 2 targets
    - shard_042: Connection timeout on source src_018
    - shard_187: Dataimport error: OutOfMemoryError
```

## Dependencies

Add to `pyproject.toml`:
```
textual>=0.50.0
```

## Testing Approach

1. Unit test `ReindexEngine` with mocked `send_request`
2. Integration test full flow with small shard count
3. UI testing via Textual's pilot framework (optional)

## Retained Helpers

These existing functions move to `reindex_engine.py`:
- `_parse_status`
- `_with_basic_auth`
- `_get_shard_doc_count`

These stay in `reindex.py`:
- `_resolve_collection`
- `_map_source_to_targets`
- `_leaders_by_shard`
- `_get_collection_from_zk`
