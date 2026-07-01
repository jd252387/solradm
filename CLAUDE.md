# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
pip install -e .                    # Install in dev mode (uv.lock present; `uv sync` also works)
uv tool install --force .           # Install sa/solradm/sad on PATH (~/.local/bin)
sa --help                           # Run CLI (aliases: solradm, sa; sad runs under debugpy on :5678)
pytest tests/                       # Run all tests
pytest tests/test_data_io.py::test_x  # Run a single test
npm run docs:dev                    # Serve VitePress docs in docs/ locally
docker compose up                   # Local Solr 9.9 + ZooKeeper for manual testing (compose.yml)
```

Requires Python 3.13+. Uses `typer>=0.26.8`. The `AsyncTyper` class is vendored
in `src/solradm/async_typer.py` (from the MIT-licensed async-typer 0.2.1) rather
than installed from PyPI: async-typer's `__init__.py` hard-imports click
pass-throughs (`clear`, `edit`, `pause`, …) that `typer>=0.21` removed, so
`import async_typer` fails on modern Typer even though the class itself works
fine. Vendoring keeps us on the latest Typer with no extra dependency.

## Architecture

**Entry point**: `src/solradm/main.py` - registers subcommand apps (`coll`, `backup`, `context`, `zoo`, `auth`, `kube`, `node`, `state`) plus top-level `status`, `current`, `version`. A non-command first arg triggers a context switch (`sa <context-name>`), like kubectl.

**Core layout**:
- `src/solradm/api/` - async aiohttp client for Solr API (BasicAuth from settings); `state.py` builds cluster state, `streaming.py` for streaming exports, `models.py` for Pydantic `Collection`/`Shard`/`Replica`.
- `src/solradm/zk/` - Kazoo ZooKeeper client (single lazy-initialized connection per session).
- `src/solradm/config/` - Dynaconf contexts in `~/.config/solradm/settings.yaml`, plus **context repositories**: shared context files (often on a network drive) merged in from `context_repositories`, enabling team-wide context sharing. `interactive/` holds first-run setup wizards.
- `src/solradm/commands/` - subcommands by domain; `commands/filters/` is the filter system for narrowing cluster state.
- `src/solradm/completion/` - shell completion functions; many connect to live ZK for suggestions.

**Data flow**: CLI args → `@with_cluster_state` fetches state from ZK → filters applied sequentially → command logic (API/ZK calls) → Rich output.

**Key patterns**:
- **Filters**: dataclasses with `field(metadata={"typer_option": ...})`, injected via `@with_cluster_state(FilterA, FilterB)` (defined in `commands/filters/utils.py`). Implement the `Filter` ABC (`init()`, `apply()`, optional `describe()`). See `collection_name_filter.py` for the canonical example.
- **Collections subapp split**: `commands/collections/subapp.py` owns the shared `app`; command modules (`lifecycle`, `maintenance`, `query`, `data_io`, `reindex`, `abort_reindex`) import that `app` and register their commands on it, and `__init__.py` imports all of them to wire it up. Follow this pattern when adding `coll` commands.
- **Reindex engine**: `commands/collections/reindex_engine.py` (shard-level orchestration) with `reindex_ui.py` for live progress; the heaviest subsystem.
- **Async commands**: vendored `AsyncTyper` (`solradm.async_typer`) + `@app.command()`, which auto-detects coroutine functions. The session is closed in `main.py`'s finally block.
- **Task rendering**: `MetaTask`/`MultiMetaTask` (`tasks/`) with `MultiTaskTable` (`renderers/`) for bulk operations with live progress.
- **Dry run**: `@with_dry_run` decorator before `@with_cluster_state`; sets `api_utils.is_dry_run` globally.

**Tests**: Heavy monkeypatching for ZK/Solr mocks. `tests/conftest.py` adds `src/` to path.

## Conventions

- `rich.print()` with styles: `[error]`, `[warning]`, `[success]`, `[blue]`
- Emoji prefixes for feedback: ✅ ❌ ⚠️ 🔄 💡
- Regex patterns for collection/node filtering (not glob)
- Absolute paths for ZK and filesystem operations
- `add_verbosity_option(app)` on new Typer apps for the `-v` flag

## Verification

Before marking a feature complete, run `sa` to verify the CLI loads without errors. Then use the command you worked on and verify it works correctly in Solr or Kubernetes. Use the `solr-mcp` MCP server for interacting with Solr during development and testing, and for confirming the command works.
