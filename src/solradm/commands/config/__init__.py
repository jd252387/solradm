import subprocess
import sys
from urllib.parse import urlparse

import typer
from kazoo.handlers.threading import KazooTimeoutError
from typer import Typer
from pathlib import Path
from typing import TYPE_CHECKING
from solradm.lazy import lazy_module

from solradm import completion
from solradm.config import settings, persist, config_path
from solradm.config.context import Context
from solradm.config.util import get_current_context, validate_config_dir
from solradm.kube.utils import (
    get_current_kubecontext,
    get_current_kubecontext_namespace,
    get_kubecontext,
)
from solradm.zk import get_client

if TYPE_CHECKING:  # pragma: no cover
    from kubernetes.client import CoreV1Api, Configuration

rich = lazy_module("rich")
pprint = lazy_module("rich.pretty").pprint
Confirm = lazy_module("rich.prompt").Confirm

app = Typer()


@app.command()
def current():
    """Show the currently active context."""

    pprint(get_current_context())


def _verify_zk_connection() -> bool:
    try:
        get_client()
        rich.print(
            f'[success]✅  Successfully connected to ZooKeeper host "{get_current_context().zk}"'
        )
        return True
    except KazooTimeoutError:
        return Confirm.ask(
            f'[warning] The ZooKeeper host "{get_current_context().zk}" is not responding. Do you still want to continue?'
        )


@app.command()
def switch(
        name: str = typer.Argument(
            ..., help="Context name", autocompletion=completion.context_names
        )
):
    """Switch to an existing context."""

    if name in [context.name for context in settings.contexts.available]:
        settings.contexts.current = {"name": name}
        if _verify_zk_connection():
            persist()
            rich.print(f'Switched to context "{name}"')
    else:
        raise typer.BadParameter(f"Context {name} does not exist!")


@app.command()
def open_config():
    """Open the configuration directory and highlight the settings file"""
    if sys.platform.startswith("win"):
        subprocess.run(["explorer", f"/select,{config_path}"])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(config_path)])
    else:
        subprocess.run(["xdg-open", str(config_path.parent)])


@app.command("config-dir")
def config_dir(
    path: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, resolve_path=True, help="Path to default configsets directory"
    ),
):
    """Update the default solradm configuration directory."""

    if not validate_config_dir(path):
        raise typer.BadParameter(
            "Directory must contain 'root' and 'configsets' subdirectories"
        )
    settings.config_dir = str(path)
    persist()
    rich.print(f"[success]✅  Updated default configuration directory to {path}")


@app.command()
def connect(
        zk: str = typer.Argument(..., help="ZooKeeper Host"),
        kubecontext: str = typer.Option(
            None, help="Kubernetes context", autocompletion=completion.kube_contexts
        ),
):
    """Temporarily connect to a ZooKeeper host."""

    settings.contexts.current = {"zk": zk}

    if kubecontext:
        if not get_kubecontext(kubecontext):
            raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")
        settings.contexts.current["kubecontext"] = kubecontext

    if _verify_zk_connection():
        persist()
        rich.print(
            "Switched to temporary context. Use [italic]context persist[/] to save the context permanently."
        )


@app.command()
def connect_current():
    """Connect using the active kubecontext and NodePort service."""

    current = get_current_kubecontext()
    if not current:
        raise typer.BadParameter("No current kubecontext configured!")

    namespace = get_current_kubecontext_namespace()
    if not namespace:
        raise typer.BadParameter(
            "The current kubecontext does not map to a specific namespace!"
        )

    from kubernetes.config import load_kube_config
    from kubernetes.client import CoreV1Api, Configuration

    load_kube_config()
    services = CoreV1Api().list_namespaced_service(namespace).items
    zk_svc = next(
        (svc for svc in services if "zk-nodeport" in svc.metadata.name),
        None,
    )

    if not zk_svc or not zk_svc.spec.ports:
        raise typer.BadParameter(
            'Could not find service with "zk-nodeport" in current namespace'
        )

    node_port = zk_svc.spec.ports[0].node_port
    api_host = urlparse(Configuration.get_default_copy().host).hostname
    if not api_host:
        raise typer.BadParameter("Unable to determine API server host")

    zk_address = f"{api_host}:{node_port}"
    connect(zk_address, current["name"])


@app.command()
def save(name: str = typer.Argument(..., help="Context name")):
    """Persist the current temporary context under a new name."""

    if "name" not in settings.contexts.current:
        add(
            name,
            settings.contexts.current.zk,
            settings.contexts.current.get("kubecontext"),
        )
    else:
        rich.print(
            f"[error]❌  You are not currently using a temporary context! The current context is {settings.contexts.current['name']}"
        )


@app.command()
def add(
        name: str = typer.Argument(..., help="Context name"),
        zk: str = typer.Option(..., "-z", "--zk", help="ZooKeeper address"),
        kubecontext: str = typer.Option(
            None,
            "-k",
            "--kubecontext",
            help="Target Kubecontext",
            autocompletion=completion.kube_contexts,
        ),
        interactive: bool = typer.Option(False, help="Interactive setup mode"),
):
    """Add a new named context."""

    if name in [context.name for context in settings.contexts.available]:
        raise typer.BadParameter(f"Context {name} already exists!")

    if interactive:
        from solradm.config.interactive.setup_context import setup
        context = setup()
    else:
        if kubecontext and not get_kubecontext(kubecontext):
            raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")
        context = Context(name=name, zk=zk, kubecontext=kubecontext)

    settings.contexts.available = settings.contexts.available + [context.as_dict()]
    persist()
    rich.print(f"[success]✅  Added new context {name}!")


@app.command()
def edit(
        name: str = typer.Argument(
            ..., help="Context name", autocompletion=completion.context_names
        ),
        zk: str = typer.Option(None, "-z", "--zk", help="ZooKeeper address"),
        kubecontext: str = typer.Option(
            None,
            "-k",
            "--kubecontext",
            help="Target Kubecontext",
            autocompletion=completion.kube_contexts,
        ),
):
    """Modify an existing context."""

    if name not in [context.name for context in settings.contexts.available]:
        raise typer.BadParameter(f"Context {name} does not exist!")

    if zk is None and kubecontext is None:
        raise typer.BadParameter("Please specify --zk and/or --kubecontext")

    if kubecontext and not get_kubecontext(kubecontext):
        raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")

    for context in settings.contexts.available:
        if context.name == name:
            new_context = Context(name, zk=zk if zk else context.zk,
                                  kubecontext=kubecontext if kubecontext else context.kubecontext)
            settings.contexts.available = [context for context in settings.contexts.available if
                                           context.name != name] + [new_context.as_dict()]
            break

    persist()
    rich.print(f"[success]✅  Updated context {name}!")


@app.command()
def delete(
        name: str = typer.Argument(
            ..., help="Context name", autocompletion=completion.context_names
        )
):
    """Remove a saved context."""

    if name not in [context.name for context in settings.contexts.available]:
        raise typer.BadParameter(f"Context {name} does not exist!")

    settings.contexts.available = [
        context for context in settings.contexts.available if context.name != name
    ]
    persist()
    rich.print(f"[success]✅  Deleted context {name}!")
