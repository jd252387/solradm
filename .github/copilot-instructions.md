# Copilot Instructions for solradm

## Project Overview
**solradm** is a CLI tool for administering Apache Solr clusters and ZooKeeper ensembles. It uses a **context system** (similar to kubectl) to manage multiple cluster connections. Built with Python 3.13+, Typer for CLI (with a vendored `AsyncTyper` for async commands), Pydantic for models, and Kazoo for ZooKeeper.

## Architecture

### Core Components
- **`src/solradm/main.py`**: Entry point. Registers all subcommand apps (`coll`, `zoo`, `context`, `kube`, etc.). Handles context shorthand switching (`sa <context-name>`).
- **`src/solradm/config/`**: Dynaconf-based configuration with context management. Contexts stored in `~/.config/solradm/settings.yaml` or shared repository files.
- **`src/solradm/api/`**: Async HTTP client for Solr API. Uses aiohttp with BasicAuth from global settings.
- **`src/solradm/zk/`**: Kazoo-based ZooKeeper client. Single lazy-initialized connection per session.
- **`src/solradm/commands/`**: Subcommand implementations organized by domain (collections, backups, kube, zk).

### Key Patterns

#### Filter System (`commands/filters/`)
Filters are dataclasses with `typer_option` metadata that get auto-injected into commands via `@with_cluster_state()`:

```python
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaStateFilter)
async def my_command(cluster_state: List[Collection], ...):
    # cluster_state is pre-filtered
```

Each filter must implement `Filter` ABC with `init()`, `apply()`, and optional `describe()` methods. See `collection_name_filter.py` for the canonical example.

#### Async Commands
Use the vendored `AsyncTyper` from `solradm.async_typer`. Commands are decorated with `@app.command()`, which auto-detects coroutine functions. The session must be closed in `main.py`'s finally block.

#### Task Rendering (`renderers/task_table.py`)
Bulk async operations use `MetaTask` + `MultiMetaTask` with live Rich tables showing progress. Pattern: create tasks, wrap in `MultiMetaTask`, call `await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(...))`.

#### Dry Run Pattern
Apply `@with_dry_run` decorator before `@with_cluster_state`. Sets `api_utils.is_dry_run = True` globally.

### Data Flow
1. CLI command invoked → Typer parses args
2. `@with_cluster_state` fetches cluster state from ZooKeeper (`api/state.py`)
3. Filters applied sequentially to narrow collections/replicas
4. Command logic executes (API calls, ZK operations)
5. Results rendered via Rich console

## Development

### Running
```bash
# Install in dev mode
pip install -e .

# Run CLI
sa --help
solradm coll --help
```

### Testing
```bash
pytest tests/
```
Tests use heavy monkeypatching to mock ZK/Solr dependencies. See `tests/conftest.py` for path setup and `test_zk_upload_filters.py` for mocking patterns.

### Adding New Commands
1. Create module in appropriate `commands/` subdirectory
2. Use `AsyncTyper()` or standard `typer.Typer()`
3. Apply `add_verbosity_option(app)` to enable `-v` flag
4. Register in parent `__init__.py` or `main.py`

### Adding New Filters
1. Create dataclass in `commands/filters/` inheriting from `Filter`
2. Use `field(metadata={"typer_option": typer.Option(...)})` for CLI args
3. Implement `init()` for validation, `apply()` for filtering logic

## Conventions
- Use `rich.print()` with semantic styles: `[error]`, `[warning]`, `[success]`, `[blue]`
- Emoji prefixes for user feedback: ✅ ❌ ⚠️ 🔄 💡
- Regex patterns for collection/node filtering (not glob)
- All paths should be absolute when interacting with ZK or local filesystem
- Completion functions live in `completion/` and connect to live ZK for suggestions
