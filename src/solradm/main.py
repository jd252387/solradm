import logging
import importlib

from async_typer import AsyncTyper
from typer.core import TyperGroup
from typer.main import get_command

from solradm.exceptions.adm_exception import AdmException
from solradm.exceptions.solr_exception import SolrException
from solradm.commands.status import status as status_cmd

lazy_subcommands = {
    "core": "solradm.commands.core",
    "coll": "solradm.commands.collections",
    "backup": "solradm.commands.backups",
    "context": "solradm.commands.config",
    "zoo": "solradm.commands.zk.editor",
    "auth": "solradm.commands.auth",
    "kube": "solradm.commands.kube",
    "node": "solradm.commands.node",
    "state": "solradm.commands.state",
}


class ConfigProxy:
    def __init__(self):
        object.__setattr__(self, "_module", None)
        object.__setattr__(self, "_patches", {})

    def _load(self):
        module = object.__getattribute__(self, "_module")
        if module is None:
            module = importlib.import_module("solradm.commands.config")
            for name, value in object.__getattribute__(self, "_patches").items():
                setattr(module, name, value)
            object.__setattr__(self, "_module", module)
        return module

    def __getattr__(self, name):
        return getattr(self._load(), name)

    def __setattr__(self, name, value):
        module = object.__getattribute__(self, "_module")
        if module is not None:
            setattr(module, name, value)
        else:
            object.__getattribute__(self, "_patches")[name] = value


config = ConfigProxy()


class LazyGroup(TyperGroup):
    def list_commands(self, ctx):
        commands = set(lazy_subcommands) | set(super().list_commands(ctx))
        return sorted(commands)

    def get_command(self, ctx, cmd_name):
        if cmd_name in lazy_subcommands:
            module = importlib.import_module(lazy_subcommands[cmd_name])
            sub_app = getattr(module, "app")
            return get_command(sub_app)
        return super().get_command(ctx, cmd_name)


app = AsyncTyper(cls=LazyGroup)
app.command()(status_cmd)


def run():
    try:
        from rich.logging import RichHandler
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True)],
        )
        import sys
        top_commands = set(lazy_subcommands) | {"status"}
        if len(sys.argv) >= 2 and not sys.argv[1].startswith("-") and sys.argv[1] not in top_commands:
            try:
                cfg = importlib.import_module("solradm.commands.config")
                cfg.switch(sys.argv[1])
            except Exception:
                import rich
                rich.print(f"Context [magenta]{sys.argv[1]}[/] doesn't exist!")
            return
        app()
    except SolrException as e:
        logging.error("Received a fatal error from Solr: %s", e)
    except AdmException as e:
        logging.error("Internal error:: %s", e)
    finally:
        from solradm.update import notify_if_outdated
        notify_if_outdated()
        from solradm.api import get_initialized_sesssion
        import asyncio
        if get_initialized_sesssion():
            asyncio.run(get_initialized_sesssion().close())


if __name__ == "__main__":
    run()
