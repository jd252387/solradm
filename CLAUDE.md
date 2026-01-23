# CLAUDE.md

## Development Commands

```bash
pip install -e .          # Install in dev mode
sa --help                 # Run CLI (alias for solradm)
pytest tests/             # Run all tests
pytest tests/test_foo.py::test_bar  # Run single test
```

## Architecture

**Entry point**: `src/solradm/main.py` - registers subcommand apps (`coll`, `zoo`, `context`, `kube`, `node`, `state`, `backup`, `auth`). Non-command first arg triggers context switch (like kubectl).

**Core layout**:
- `src/solradm/api/` - async aiohttp client for Solr API (BasicAuth from settings)
- `src/solradm/zk/` - Kazoo ZooKeeper client (lazy-initialized)
- `src/solradm/config/` - Dynaconf contexts stored in `~/.config/solradm/settings.yaml`
- `src/solradm/commands/` - subcommands by domain
- `src/solradm/commands/filters/` - filter system for narrowing cluster state

**Data flow**: CLI args → `@with_cluster_state` fetches state from ZK → filters applied sequentially → command logic (API/ZK calls) → Rich output

**Key patterns**:
- **Filters**: dataclasses with `typer_option` metadata, injected via `@with_cluster_state(FilterA, FilterB)`. Implement `Filter` ABC (`init()`, `apply()`, optional `describe()`). See `collection_name_filter.py`.
- **Async commands**: `AsyncTyper` + `@app.async_command()`. Session closed in `main.py` finally block.
- **Task rendering**: `MetaTask`/`MultiMetaTask` with `MultiTaskTable` for bulk operations with live progress.
- **Dry run**: `@with_dry_run` decorator before `@with_cluster_state`.

**Tests**: Heavy monkeypatching for ZK/Solr mocks. `tests/conftest.py` adds `src/` to path.

## Conventions

- `rich.print()` with styles: `[error]`, `[warning]`, `[success]`, `[blue]`
- Emoji prefixes for feedback: ✅ ❌ ⚠️ 🔄 💡
- Regex patterns for collection/node filtering (not glob)
- Absolute paths for ZK and filesystem operations
- `add_verbosity_option(app)` on new Typer apps for `-v` flag

## Verification

Before marking a feature complete, run `sa` to verify the CLI loads without errors. Then use the command you worked on, and verify it works correctly in Solr or Kubernetes. Use the `solr-mcp` MCP server for interacting with Solr during development and testing, and for making sure the command works. 
