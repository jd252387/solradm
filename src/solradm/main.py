import logging

import os

import rich
from async_typer import AsyncTyper
from rich.logging import RichHandler
from lazy_loader import load as lazy_load

config = lazy_load("solradm.commands.config")
collections = lazy_load("solradm.commands.collections")
backups = lazy_load("solradm.commands.backups")
auth = lazy_load("solradm.commands.auth")
node = lazy_load("solradm.commands.node")
state = lazy_load("solradm.commands.state")
kube = lazy_load("solradm.commands.kube")
editor = lazy_load("solradm.commands.zk.editor")
from solradm.commands.status import status as status_cmd
from solradm.exceptions.adm_exception import AdmException
from solradm.exceptions.solr_exception import SolrException

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

app = AsyncTyper()


def _is_completing() -> bool:
    """Return True when the CLI runs in shell-completion mode."""
    return any(key.endswith("_COMPLETE") for key in os.environ)

app.add_typer(collections.app, name="coll", help="Interact with the Collections API")
app.add_typer(backups.app, name="backup", help="Take or restore backups using the Replication API")
app.add_typer(config.app, name="context", help="Manage solradm Contexts")
app.add_typer(editor.app, name="zoo", help="Manage ZooKeeper")
app.add_typer(auth.app, name="auth", help="Manage Solr authentication")
app.add_typer(kube.app, name="kube", help="Manage Kubernetes workloads")
app.add_typer(node.app, name="node", help="Manage Solr nodes")
app.add_typer(state.app, name="state", help="Export or restore cluster state")
app.command()(status_cmd)


def run():
    try:
        import sys

        top_commands = {"core", "coll", "backup", "context", "zoo", "auth", "kube", "node", "state", "status"}
        if len(sys.argv) >= 2 and not sys.argv[1].startswith("-") and sys.argv[1] not in top_commands:
            try:
                config.switch(sys.argv[1])
            except Exception as e:
                rich.print(f"Context [magenta]{sys.argv[1]}[/] doesn't exist!")
            return
        app()
    except SolrException as e:
        logging.error("Received a fatal error from Solr: %s", e)
    except AdmException as e:
        logging.error("Internal error:: %s", e)
    finally:
        if not _is_completing():
            from solradm.update import notify_if_outdated
            from solradm.api import get_initialized_session

            notify_if_outdated()
            import asyncio
            if get_initialized_session():
                asyncio.run(get_initialized_session().close())


if __name__ == "__main__":
    run()
