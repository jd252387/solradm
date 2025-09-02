import logging

import rich
from rich.logging import RichHandler

from solradm.api import get_initialized_sesssion
from solradm.exceptions.adm_exception import AdmException
from solradm.exceptions.solr_exception import SolrException
from solradm.lazy_group import LazyGroup
from solradm.update import notify_if_outdated


class _ConfigProxy:
    def __getattr__(self, name):
        from solradm.commands import config as real_config

        return getattr(real_config, name)


config = _ConfigProxy()

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

app = LazyGroup(help="Solr Administration CLI")

app.add_lazy_typer(
    "solradm.commands.collections:app",
    name="coll",
    help="Interact with the Collections API",
)
app.add_lazy_typer(
    "solradm.commands.backups:app",
    name="backup",
    help="Take or restore backups using the Replication API",
)
app.add_lazy_typer(
    "solradm.commands.config:app",
    name="context",
    help="Manage solradm Contexts",
)
app.add_lazy_typer(
    "solradm.commands.zk.editor:app",
    name="zoo",
    help="Manage ZooKeeper",
)
app.add_lazy_typer(
    "solradm.commands.auth:app",
    name="auth",
    help="Manage Solr authentication",
)
app.add_lazy_typer(
    "solradm.commands.kube:app",
    name="kube",
    help="Manage Kubernetes workloads",
)
app.add_lazy_typer(
    "solradm.commands.node:app",
    name="node",
    help="Manage Solr nodes",
)
app.add_lazy_typer(
    "solradm.commands.state:app",
    name="state",
    help="Export or restore cluster state",
)
app.add_lazy_command(
    "solradm.commands.status:status",
    name="status",
    help="Show cluster status",
)


def run():
    try:
        import sys

        top_commands = {
            "core",
            "coll",
            "backup",
            "context",
            "zoo",
            "auth",
            "kube",
            "node",
            "state",
            "status",
        }
        if (
            len(sys.argv) >= 2
            and not sys.argv[1].startswith("-")
            and sys.argv[1] not in top_commands
        ):
            try:
                config.switch(sys.argv[1])
            except Exception:
                rich.print(f"Context [magenta]{sys.argv[1]}[/] doesn't exist!")
            return
        app()
    except SolrException as e:
        logging.error("Received a fatal error from Solr: %s", e)
    except AdmException as e:
        logging.error("Internal error:: %s", e)
    finally:
        notify_if_outdated()
        import asyncio
        if get_initialized_sesssion():
            asyncio.run(get_initialized_sesssion().close())


if __name__ == "__main__":
    run()
