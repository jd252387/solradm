"""Entry point for the solradm CLI.

The module keeps startup and autocompletion fast by avoiding importing heavy
command modules until they are needed. A small placeholder ``AsyncTyper`` is
registered for each sub-command so that top-level completion works without
loading the actual implementations.
"""

from __future__ import annotations

import logging
import os
from importlib import import_module

from async_typer import AsyncTyper

from solradm.exceptions.adm_exception import AdmException
from solradm.exceptions.solr_exception import SolrException


class _ConfigProxy:
    """Lazily import the config command module when needed."""

    module = "solradm.commands.config"

    def switch(self, name: str) -> None:  # pragma: no cover - simple delegation
        import_module(self.module).switch(name)


config = _ConfigProxy()


def _is_completing() -> bool:
    """Return True when the CLI runs in shell-completion mode."""

    return any(key.endswith("_COMPLETE") for key in os.environ)


# Mapping of CLI command names to their implementing modules and help text.
LAZY_TYPERS: dict[str, tuple[str, str]] = {
    "coll": ("solradm.commands.collections", "Interact with the Collections API"),
    "backup": (
        "solradm.commands.backups",
        "Take or restore backups using the Replication API",
    ),
    "context": ("solradm.commands.config", "Manage solradm Contexts"),
    "zoo": ("solradm.commands.zk.editor", "Manage ZooKeeper"),
    "auth": ("solradm.commands.auth", "Manage Solr authentication"),
    "kube": ("solradm.commands.kube", "Manage Kubernetes workloads"),
    "node": ("solradm.commands.node", "Manage Solr nodes"),
    "state": ("solradm.commands.state", "Export or restore cluster state"),
}

LAZY_COMMANDS: dict[str, tuple[str, str, str]] = {
    "status": (
        "solradm.commands.status",
        "status",
        "Display status table for replicas",
    )
}


app = AsyncTyper()

# Register lightweight placeholders for all commands so that ``--help`` and
# top-level completion know about them without importing the heavy modules.
for name, (_, help_text) in LAZY_TYPERS.items():
    app.add_typer(AsyncTyper(), name=name, help=help_text)


def _placeholder() -> None:  # pragma: no cover - replaced at runtime
    """Placeholder callback that gets replaced when the real command loads."""


for name, (_, _, help_text) in LAZY_COMMANDS.items():
    app.command(name=name, help=help_text)(_placeholder)


def _load_command(name: str) -> None:
    """Load the real implementation for ``name`` and replace the placeholder."""

    if not hasattr(app, "registered_groups"):
        # ``app`` was replaced (e.g. during tests), nothing to load.
        return

    if name in LAZY_TYPERS:
        module_name, _ = LAZY_TYPERS[name]
        module = import_module(module_name)
        for info in app.registered_groups:
            if info.name == name:
                info.typer_instance = module.app
                break
    elif name in LAZY_COMMANDS:
        module_name, func_name, _ = LAZY_COMMANDS[name]
        module = import_module(module_name)
        func = getattr(module, func_name)
        for info in app.registered_commands:
            if info.name == name:
                info.callback = func
                break


def run() -> None:  # pragma: no cover - entrypoint executed via CLI
    ran_app = False
    try:
        import sys

        if not _is_completing():
            # Import rich and configure logging only when not completing to
            # avoid the overhead during shell completion.
            import rich
            from rich.logging import RichHandler

            logging.basicConfig(
                level=logging.INFO,
                format="%(message)s",
                datefmt="[%X]",
                handlers=[RichHandler(rich_tracebacks=True)],
            )

        top_commands = set(LAZY_TYPERS) | set(LAZY_COMMANDS)

        cmd: str | None = None
        if len(sys.argv) >= 2 and not sys.argv[1].startswith("-"):
            cmd = sys.argv[1]
        elif _is_completing():
            from click.shell_completion import split_arg_string

            comp_words = os.environ.get("COMP_WORDS", "")
            words = split_arg_string(comp_words)
            if len(words) >= 2:
                cmd = words[1]

        if cmd:
            if cmd in top_commands:
                _load_command(cmd)
            else:
                try:
                    config.switch(cmd)
                except Exception:
                    import rich

                    rich.print(f"Context [magenta]{cmd}[/] doesn't exist!")
                return
        elif not _is_completing() and "--help" not in sys.argv and "-h" not in sys.argv:
            # Pre-load commands when running the CLI without specifying a
            # command to provide full help and validation.
            for name in top_commands:
                _load_command(name)

        app()
        ran_app = hasattr(app, "registered_groups")
    except SolrException as e:  # pragma: no cover - user-facing errors
        logging.error("Received a fatal error from Solr: %s", e)
    except AdmException as e:  # pragma: no cover - user-facing errors
        logging.error("Internal error:: %s", e)
    finally:
        if ran_app and not _is_completing():
            from solradm.update import notify_if_outdated
            from solradm.api import get_initialized_session

            notify_if_outdated()
            import asyncio

            if get_initialized_session():
                asyncio.run(get_initialized_session().close())


if __name__ == "__main__":  # pragma: no cover
    run()

