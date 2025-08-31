import rich
import typer
from async_typer import AsyncTyper
from kazoo.handlers.threading import KazooTimeoutError
from kubernetes.config import list_kube_config_contexts
from typer import Typer

from solradm.config import settings, persist
from solradm.config.context import Context
from solradm.config.interactive.setup_context import setup
from solradm.config.util import get_current_context
from solradm.kube.utils import get_kubecontext
from solradm.zk import get_client
from rich.pretty import pprint
from rich.prompt import Confirm

app = Typer()


@app.command()
def current():
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
def switch(name: str = typer.Argument(..., help="Context name")) -> bool:
    if name in [context.name for context in settings.contexts.available]:
        settings.contexts.current = {"name": name}
        if _verify_zk_connection():
            persist()
            rich.print(f'Switched to context "{name}"')
    else:
        raise typer.BadParameter(f"Context {name} does not exist!")


@app.command()
def connect(
    zk: str = typer.Argument(..., help="ZooKeeper Host"),
    kubecontext: str = typer.Option(None, help="Kubernetes context"),
):
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
def save(name: str = typer.Argument(..., help="Context name")):
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
        None, "-k", "--kubecontext", help="Target Kubecontext"
    ),
    interactive: bool = typer.Option(False, help="Interactive setup mode"),
):
    if name in [context.name for context in settings.contexts.available]:
        raise typer.BadParameter(f"Context {name} already exists!")

    if interactive:
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
    name: str = typer.Argument(..., help="Context name"),
    zk: str = typer.Option(None, "-z", "--zk", help="ZooKeeper address"),
    kubecontext: str = typer.Option(
        None, "-k", "--kubecontext", help="Target Kubecontext"
    ),
):
    if name not in [context.name for context in settings.contexts.available]:
        raise typer.BadParameter(f"Context {name} does not exist!")

    if zk is None and kubecontext is None:
        raise typer.BadParameter("Please specify --zk and/or --kubecontext")

    if kubecontext and not get_kubecontext(kubecontext):
        raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")

    for context in settings.contexts.available:
        if context.name == name:
            if zk:
                context.zk = zk
            if kubecontext:
                context.kubecontext = kubecontext
            break

    persist()
    rich.print(f"[success]✅  Updated context {name}!")


@app.command()
def delete(name: str = typer.Argument(...)):
    if name not in [context.name for context in settings.contexts.available]:
        raise typer.BadParameter(f"Context {name} does not exist!")

    settings.contexts.available = [
        context for context in settings.contexts.available if context.name != name
    ]
    persist()
    rich.print(f"[success]✅  Deleted context {name}!")
