import logging

from async_typer import AsyncTyper
from rich.logging import RichHandler

from solradm.api import get_initialized_sesssion
from solradm.commands import config, core, collections, backups, auth, kube
from solradm.commands.zk import editor
from solradm.exceptions.adm_exception import AdmException
from solradm.exceptions.solr_exception import SolrException
from solradm.update import notify_if_outdated

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)]
)

app = AsyncTyper()

app.add_typer(core.app, name="core", help="Interact with the Core API")
app.add_typer(collections.app, name="coll", help="Interact with the Collections API")
app.add_typer(backups.app, name="backup", help="Take or restore backups using the Replication API")
app.add_typer(config.app, name="context", help="Manage solradm Contexts")
app.add_typer(editor.app, name="zoo", help="Manage ZooKeeper")
app.add_typer(auth.app, name="auth", help="Manage Solr authentication")
app.add_typer(kube.app, name="kube", help="Manage Kubernetes workloads")

def run():
    try:
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
