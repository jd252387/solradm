import importlib
import logging
import sys
from typing import Dict, Tuple

from async_typer import AsyncTyper
from typer.core import TyperGroup
import typer


# Map top-level command names to the module path and attribute providing the command.
LAZY_COMMANDS: Dict[str, Tuple[str, str]] = {
    "coll": ("solradm.commands.collections", "app"),
    "backup": ("solradm.commands.backups", "app"),
    "context": ("solradm.commands.config", "app"),
    "zoo": ("solradm.commands.zk.editor", "app"),
    "auth": ("solradm.commands.auth", "app"),
    "kube": ("solradm.commands.kube", "app"),
    "node": ("solradm.commands.node", "app"),
    "state": ("solradm.commands.state", "app"),
    "status": ("solradm.commands.status", "status"),
}


class LazyGroup(TyperGroup):
    """Typer Group that loads commands only when needed."""

    def list_commands(self, ctx):  # pragma: no cover - simple delegation
        commands = list(super().list_commands(ctx))
        for name in LAZY_COMMANDS:
            if name not in commands:
                commands.append(name)
        return commands

    def get_command(self, ctx, name):
        command = super().get_command(ctx, name)
        if command is not None:
            return command
        target = LAZY_COMMANDS.get(name)
        if not target:
            return None
        module_path, attr = target
        module = importlib.import_module(module_path)
        obj = getattr(module, attr)
        if isinstance(obj, typer.Typer):
            command = typer.main.get_command(obj)
        else:
            from typer.main import get_command_from_info, DEFAULT_MARKUP_MODE
            from typer.models import CommandInfo

            command = get_command_from_info(
                CommandInfo(name=name, callback=obj),
                pretty_exceptions_short=True,
                rich_markup_mode=DEFAULT_MARKUP_MODE,
            )
        self.add_command(command, name)
        return command


app = AsyncTyper(cls=LazyGroup)


@app.callback()
def _root_callback() -> None:
    """Solr Administration CLI."""
    pass


def run():
    from solradm.exceptions.adm_exception import AdmException
    from solradm.exceptions.solr_exception import SolrException

    try:
        top_commands = set(LAZY_COMMANDS) | {"_complete"}
        if (
            len(sys.argv) >= 2
            and not sys.argv[1].startswith("-")
            and sys.argv[1] not in top_commands
        ):
            try:
                config_mod = importlib.import_module("solradm.commands.config")
                config_mod.switch(sys.argv[1])
            except Exception:
                import rich

                rich.print(
                    f"Context [magenta]{sys.argv[1]}[/] doesn't exist!"
                )
            return

        from rich.logging import RichHandler

        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True)],
        )

        app()
    except SolrException as e:
        logging.error("Received a fatal error from Solr: %s", e)
    except AdmException as e:
        logging.error("Internal error:: %s", e)
    finally:
        from solradm.update import notify_if_outdated

        notify_if_outdated()
        if "solradm.api" in sys.modules:
            from solradm.api import get_initialized_sesssion
            import asyncio

            if get_initialized_sesssion():
                asyncio.run(get_initialized_sesssion().close())


if __name__ == "__main__":
    run()


def __getattr__(name: str):
    if name == "config":
        module = importlib.import_module("solradm.commands.config")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__} has no attribute {name}")

