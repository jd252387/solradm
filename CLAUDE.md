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

**Entry point**: `src/solradm/main.py` - registers subcommand apps (`coll`, `backup`, `context`, `zoo`, `auth`, `kube`, `node`, `state`, `rt`) plus top-level `status`, `current`, `version`. A non-command first arg triggers a context switch (`sa <context-name>`), like kubectl. That dispatch uses a **hardcoded `top_commands` set in `run()`** (`main.py:45`) — a new top-level command or subapp must be added there too, or `sa <name>` will be swallowed as a failed context switch. `run()`'s finally block also calls `notify_if_outdated()` (`update.py`), which shells out to `pip index versions solradm` and caches the result for an hour in `~/.cache/solradm/update.json`.

**Core layout**:
- `src/solradm/api/` - async aiohttp client for Solr API (BasicAuth from settings); `state.py` builds cluster state, `streaming.py` for streaming exports, `models.py` for Pydantic `Collection`/`Shard`/`Replica`.
- `src/solradm/zk/` - Kazoo ZooKeeper client (single lazy-initialized connection per session).
- `src/solradm/config/` - Dynaconf contexts in `~/.config/solradm/settings.yaml`, plus **context repositories**: shared context files (often on a network drive) merged in from `context_repositories`, enabling team-wide context sharing. `interactive/` holds first-run setup wizards.
- `src/solradm/commands/` - subcommands by domain; `commands/filters/` is the filter system for narrowing cluster state.
- `src/solradm/completion/` - shell completion functions; many connect to live ZK for suggestions.
- `src/solradm/commands/rt/` - `rt` subcommand: builds and ships offline Artifactory dependency bundles. `resolver.py` is a Maven POM walker (ported from `airgap/offline_deps.py`) that resolves a Gradle-notation coordinate's transitive compile+runtime closure without full nearest-wins conflict resolution — it deliberately over-fetches so an offline mirror stays complete.

**Data flow**: CLI args → `@with_cluster_state` fetches state from ZK → filters applied sequentially → command logic (API/ZK calls) → Rich output.

**Key patterns**:
- **Filters**: dataclasses with `field(metadata={"typer_option": ...})`, injected via `@with_cluster_state(FilterA, FilterB)` (defined in `commands/filters/utils.py`). Implement the `Filter` ABC (`init()`, `apply()`, optional `describe()`). See `collection_name_filter.py` for the canonical example.
- **Collections subapp split**: `commands/collections/subapp.py` owns the shared `app`; command modules (`lifecycle`, `maintenance`, `query`, `data_io`, `reindex`, `abort_reindex`, `kill`, `export_state`) import that `app` and register their commands on it, and `__init__.py` imports all of them to wire it up. Follow this pattern when adding `coll` commands — a module missing from `__init__.py`'s import list is silently unregistered. `commands/rt/` uses the same split (`rt/subapp.py`).
- **Reindex engine**: `commands/collections/reindex_engine.py` (shard-level orchestration) with `reindex_ui.py` for live progress; the heaviest subsystem.
- **Async commands**: vendored `AsyncTyper` (`solradm.async_typer`) + `@app.command()`, which auto-detects coroutine functions. The session is closed in `main.py`'s finally block.
- **Task rendering**: `MetaTask`/`MultiMetaTask` (`tasks/`) with `MultiTaskTable` (`renderers/`) for bulk operations with live progress.
- **Dry run**: `@with_dry_run` decorator before `@with_cluster_state`; sets `api_utils.is_dry_run` globally.

**Tests**: Heavy monkeypatching for ZK/Solr mocks. `tests/conftest.py` adds `src/` to path.

## Command Tree

```
sa
├── status                  top-level: overall cluster status
├── current                 top-level: show the currently active context
├── version                 top-level: print solradm version
├── <context-name>          kubectl-style shorthand for `context connect <name>`
│
├── coll                    commands/collections/
│   ├── create              lifecycle.py
│   ├── delete              lifecycle.py
│   ├── populate            lifecycle.py
│   ├── depopulate          lifecycle.py
│   ├── reload              maintenance.py
│   ├── query               query.py
│   ├── export-documents    data_io.py
│   ├── import              data_io.py (import_documents)
│   ├── reindex             reindex.py (launches reindex_engine.py + reindex_ui.py TUI)
│   ├── abort-reindex       abort_reindex.py
│   ├── kill                kill.py — delete the K8s pods hosting selected cores
│   └── export-state        export_state.py — write selected cores to a file (-o field list)
│
├── backup                  commands/backups/
│   ├── take                create backups for filtered replicas
│   ├── restore             restore backups (single collection only)
│   └── restore-status      show restore progress for filtered replicas
│
├── context                 commands/config/ (app registered under the name "context")
│   ├── open-config         open the settings.yaml file
│   ├── connect             connect to a context by name
│   ├── connect-current     reconnect to the current context
│   ├── save                save current connection as a named context
│   ├── add                 add a new context (interactive)
│   ├── edit                edit an existing context
│   ├── remove              delete a context
│   ├── list                list all contexts
│   └── repo                manage context repositories (shared/network contexts)
│       ├── create          create a new repo file
│       ├── add             register an existing repo path
│       ├── remove          unregister a repo
│       ├── list            list registered repos
│       ├── open            open a repo file
│       └── upload          upload local context to a repo
│
├── zoo                     commands/zk/editor.py ("zoo" = ZooKeeper)
│   ├── edit                edit a znode's content in $EDITOR
│   ├── view                view a znode's content
│   ├── render              render Jinja templates over config subdirs to a rendered/ dir
│   ├── diff                diff local configsets against ZooKeeper
│   ├── upload              upload local files/directories to a znode
│   └── sync                watch a local dir and live-sync changes to ZooKeeper
│
├── auth                    commands/auth/
│   ├── edit                edit Solr authentication/authorization znodes
│   └── view                view Solr authentication/authorization znodes
│
├── kube                    commands/kube/ (Kubernetes workload management)
│   ├── logs                stream logs for matching pods
│   ├── disk                show /var/solr/data disk usage for solr-cloud pods
│   ├── suspend             scale matching workloads to zero, saving replica counts
│   ├── resume              restore replicas from a saved suspend state
│   ├── dir                 open the directory storing saved kube workload states
│   └── ui                  open the OpenShift console for the current namespace
│
├── node                    commands/node/
│   ├── drain               remove cores not in selected collections from nodes
│   └── ui                  open the overseer-elected node's Solr UI in a browser
│
├── state                   commands/state/ (cluster state snapshot)
│   ├── export              export cluster state to a file
│   └── import              restore cluster state from a snapshot file
│
└── rt                      commands/rt/ (offline Artifactory dependency bundles)
    ├── setup               configure and persist an Artifactory profile
    ├── download            resolve a Gradle project's deps and produce an offline bundle zip
    ├── artifact            download Maven artifacts by Gradle coordinate (no Gradle project needed)
    └── upload              upload a previously built bundle into Artifactory via `jf`
```

Most `coll`/`backup`/`node` commands accept the shared filter options (collection/node/shard/replica-state/replica-type/replica-position patterns) injected by `@with_cluster_state`, plus `--dry-run` where destructive.

## Conventions

- `rich.print()` with styles: `[error]`, `[warning]`, `[success]`, `[blue]`
- Emoji prefixes for feedback: ✅ ❌ ⚠️ 🔄 💡
- Regex patterns for collection/node filtering (not glob)
- Absolute paths for ZK and filesystem operations
- `add_verbosity_option(app)` on new Typer apps for the `-v` flag

## Releasing

`version` is duplicated in `pyproject.toml` and `package.json` — bump both together. Publishing is triggered by a published GitHub release: `.github/workflows/publish.yml` fails the build unless the release tag (minus a leading `v`) equals the `pyproject.toml` version, then runs `uv build` + `uv publish` to PyPI.

The VitePress reference in `docs/commands.md` is hand-maintained (not generated) and currently lags the CLI — `rt`, `coll kill`, `coll export-state`, `coll import`/`export-documents`, and `node ui` are undocumented. Update it when adding or changing a command.

## Verification

Before marking a feature complete, run `sa` to verify the CLI loads without errors. Then use the command you worked on and verify it works correctly in Solr or Kubernetes. Use the `solr-mcp` MCP server for interacting with Solr during development and testing, and for confirming the command works.
