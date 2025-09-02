import importlib
import logging
import os

from async_typer import AsyncTyper
import typer
from typer.core import TyperGroup

from solradm.exceptions.adm_exception import AdmException
from solradm.exceptions.solr_exception import SolrException


class _LazyModule:
    def __init__(self, module_name: str):
        self._module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self._module_name)
        return self._module

    def __getattr__(self, item):
        return getattr(self._load(), item)


config = _LazyModule("solradm.commands.config")


class LazyGroup(TyperGroup):
    lazy_commands = {
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

    def list_commands(self, ctx):
        return sorted(set(super().list_commands(ctx) + list(self.lazy_commands.keys())))

    def get_command(self, ctx, name):
        if name in self.lazy_commands:
            module_path, attr = self.lazy_commands[name]
            module = importlib.import_module(module_path)
            cmd = getattr(module, attr)
            if isinstance(cmd, (AsyncTyper, typer.Typer)):
                command = typer.main.get_command(cmd)
            else:
                temp_app = AsyncTyper()
                temp_app.command(name=name)(cmd)
                command = typer.main.get_command(temp_app)
            command.name = name
            return command
        return super().get_command(ctx, name)


app = AsyncTyper(cls=LazyGroup)


@app.callback()
def _callback():
    """Main entrypoint."""
    pass


def run():
    try:
        import sys

        top_commands = set(LazyGroup.lazy_commands.keys())
        if len(sys.argv) >= 2 and not sys.argv[1].startswith("-") and sys.argv[1] not in top_commands:
            try:
                config.switch(sys.argv[1])
            except Exception:
                if "_SOLRADM_COMPLETE" not in os.environ:
                    import rich

                    rich.print(
                        f"Context [magenta]{sys.argv[1]}[/] doesn't exist!"
                    )
            return

        if "_SOLRADM_COMPLETE" not in os.environ:
            import rich
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
        if "_SOLRADM_COMPLETE" not in os.environ:
            from solradm.update import notify_if_outdated
            from solradm.api import get_initialized_sesssion
            import asyncio

            notify_if_outdated()
            if get_initialized_sesssion():
                asyncio.run(get_initialized_sesssion().close())


if __name__ == "__main__":
    run()
