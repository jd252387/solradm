import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import rich
import typer
from kazoo.handlers.threading import KazooTimeoutError
from kubernetes.client import CoreV1Api, Configuration
from kubernetes.config import load_kube_config
from rich.pretty import pprint
from rich.prompt import Confirm, Prompt
from rich.table import Table
from typer import Typer

from solradm import completion
from solradm.completion.contexts import context_names, context_repo_paths, kube_contexts
from solradm.config import settings, persist, config_path, local_contexts
from solradm.config.context import Context
from solradm.config.interactive.setup_context import setup
from solradm.config.util import (
    get_current_context,
    validate_config_dir,
    is_valid_context_repo,
    load_repo_contexts,
    save_repo_contexts,
)
from solradm.kube.utils import (
    get_current_kubecontext,
    get_current_kubecontext_namespace,
    get_kubecontext,
)
from solradm.zk import get_client

app = Typer()
repo_app = Typer(help="Manage context repositories.")
app.add_typer(repo_app, name="repo")


@app.command()
def current():
    """Show the currently active context."""

    pprint(get_current_context())


def verify_zk_connection() -> bool:
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
            ..., help="Context name", autocompletion=context_names
        )
):
    """Switch to an existing context."""

    if name in [context.name for context in settings.contexts.available]:
        settings.contexts.current = {"name": name}
        if verify_zk_connection():
            persist()
            if name in [c["name"] for c in local_contexts]:
                location = "local configuration"
            else:
                repo_list = list(settings.get("context_repositories") or [])
                location = "unknown location"
                for repo in reversed(repo_list):
                    repo_path = Path(repo)
                    contexts = load_repo_contexts(repo_path)
                    if any(c["name"] == name for c in contexts):
                        location = f"repository {repo_path}"
                        break
            rich.print(f'Switched to context "{name}" from {location}')
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


@repo_app.command("add")
def add_repo(
        path: Path = typer.Argument(
            ..., exists=True, file_okay=True, dir_okay=False, resolve_path=True, help="Path to context repository"
        ),
):
    """Add a new context repository."""

    repo_list = list(settings.get("context_repositories") or [])
    path_str = str(path)
    if path_str in repo_list:
        raise typer.BadParameter(f"Context repository {path} already exists!")

    if not is_valid_context_repo(path):
        raise typer.BadParameter(f"{path} is not a valid context repository")

    repo_list.append(path_str)
    settings.context_repositories = repo_list
    persist(repo_list)
    settings.configure(settings_files=repo_list + [config_path])
    settings.reload()
    rich.print(f"[success]✅  Added context repository {path}!")


@repo_app.command("remove")
def remove_repo(
        path: Path = typer.Argument(
            ..., exists=True, file_okay=True, dir_okay=False, autocompletion=context_repo_paths,
            help="Path to context repository"
        ),
):
    """Remove a context repository."""

    repo_list = list(settings.get("context_repositories") or [])
    path_str = str(path)
    if path_str not in repo_list:
        raise typer.BadParameter(f"Context repository {path} does not exist!")

    repo_list.remove(path_str)
    settings.context_repositories = repo_list
    persist(repo_list)
    settings.configure(settings_files=repo_list + [config_path])
    settings.reload()
    rich.print(f"[success]✅  Deleted context repository {path}!")


@repo_app.command("list")
def list_repos():
    """List configured context repositories and their contexts."""

    repo_list = list(settings.get("context_repositories") or [])
    table = Table("Repository", "Contexts")
    for repo in repo_list:
        repo_path = Path(repo)
        contexts = [c["name"] for c in load_repo_contexts(repo_path)]
        table.add_row(str(repo_path), ", ".join(contexts) if contexts else "-")
    rich.print(table)


@repo_app.command("open")
def open_repo(
        path: Path = typer.Argument(
            ..., exists=True, file_okay=True, dir_okay=False, autocompletion=context_repo_paths,
            help="Path to context repository"
        ),
):
    """Open the location of a configured context repository."""

    repo_list = list(settings.get("context_repositories") or [])
    path_str = str(path)
    if path_str not in repo_list:
        raise typer.BadParameter(f"Context repository {path} is not configured!")

    if sys.platform.startswith("win"):
        subprocess.run(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)])
    else:
        subprocess.run(["xdg-open", str(path.parent)])


@app.command("config-dir")
def config_dir(
        path: Path = typer.Argument(
            ..., exists=True, file_okay=False, dir_okay=True, resolve_path=True,
            help="Path to default configsets directory"
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
            None, help="Kubernetes context", autocompletion=kube_contexts
        ),
):
    """Temporarily connect to a ZooKeeper host."""

    settings.contexts.current = {"zk": zk}

    if kubecontext:
        if not get_kubecontext(kubecontext):
            raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")
        settings.contexts.current["kubecontext"] = kubecontext

    if verify_zk_connection():
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
        name: str = typer.Argument(None, help="Context name"),
        zk: str = typer.Option(None, "-z", "--zk", help="ZooKeeper address"),
        kubecontext: str = typer.Option(
            None,
            "-k",
            "--kubecontext",
            help="Target Kubecontext",
            autocompletion=kube_contexts,
        ),
        interactive: bool = typer.Option(True, help="Interactive setup mode"),
):
    """Add a new named context."""
    if interactive:
        context_name = ""
        while context_name == "":
            context_name = Prompt.ask("[question]Enter your context name -> ")
            if name in [context.name for context in settings.contexts.available]:
                rich.print(f"[error] Context {name} already exists!")
                context_name = ""
        context = setup(context_name)
    else:
        if not name or not zk:
            raise typer.BadParameter("You must specify both a name and a ZooKeeper address! Alternatively, use --interactive to enter interactive setup mode.")
        if name in [context.name for context in settings.contexts.available]:
            raise typer.BadParameter(f"Context {name} already exists!")
        if kubecontext and not get_kubecontext(kubecontext):
            raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")
        context = Context(name=name, zk=zk, kubecontext=kubecontext)

    settings.contexts.available = settings.contexts.available + [context.as_dict()]
    local_contexts.append(context.as_dict())
    persist()
    rich.print(f"[success]✅ Added new context {name}!")


@app.command()
def edit(
        name: str = typer.Argument(
            ..., help="Context name", autocompletion=context_names
        ),
        zk: str = typer.Option(None, "-z", "--zk", help="ZooKeeper address"),
        kubecontext: str = typer.Option(
            None,
            "-k",
            "--kubecontext",
            help="Target Kubecontext",
            autocompletion=kube_contexts,
        ),
):
    """Modify an existing context."""

    if zk is None and kubecontext is None:
        raise typer.BadParameter("Please specify --zk and/or --kubecontext")

    if kubecontext and not get_kubecontext(kubecontext):
        raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")

    if name in [c["name"] for c in local_contexts]:
        for context in settings.contexts.available:
            if context.name == name:
                new_context = Context(
                    name,
                    zk=zk if zk else context.zk,
                    kubecontext=kubecontext if kubecontext else context.get("kubecontext"),
                )
                settings.contexts.available = [
                                                  c for c in settings.contexts.available if c.name != name
                                              ] + [new_context.as_dict()]
                break

        for idx, c in enumerate(local_contexts):
            if c["name"] == name:
                local_contexts[idx] = new_context.as_dict()
                break

        persist()
        rich.print(f"[success]✅  Updated context {name}!")
    else:
        repo_list = list(settings.get("context_repositories") or [])
        target_repo = None
        repo_contexts = None
        for repo in reversed(repo_list):
            repo_path = Path(repo)
            contexts = load_repo_contexts(repo_path)
            if any(c["name"] == name for c in contexts):
                target_repo = repo_path
                repo_contexts = contexts
                break
        if not target_repo:
            raise typer.BadParameter(f"Context {name} does not exist!")

        existing = next(c for c in repo_contexts if c["name"] == name)
        new_context = Context(
            name,
            zk=zk if zk else existing["zk"],
            kubecontext=kubecontext if kubecontext else existing.get("kubecontext"),
        )
        repo_contexts = [
            c if c["name"] != name else new_context.as_dict() for c in repo_contexts
        ]
        save_repo_contexts(target_repo, repo_contexts)
        settings.reload()
        rich.print(f"[success]✅  Updated context {name} in {target_repo}!")


@app.command()
@app.command("remove")
@app.command("delete")
def delete(
        name: str = typer.Argument(
            ..., help="Context name", autocompletion=context_names
        )
):
    """Remove a saved context."""

    if name in [c["name"] for c in local_contexts]:
        settings.contexts.available = [
            context for context in settings.contexts.available if context.name != name
        ]
        local_contexts[:] = [c for c in local_contexts if c["name"] != name]
        persist()
        rich.print(f"[success]✅  Deleted context {name}!")
    else:
        repo_list = list(settings.get("context_repositories") or [])
        target_repo = None
        repo_contexts = None
        for repo in reversed(repo_list):
            repo_path = Path(repo)
            contexts = load_repo_contexts(repo_path)
            if any(c["name"] == name for c in contexts):
                target_repo = repo_path
                repo_contexts = contexts
                break
        if not target_repo:
            raise typer.BadParameter(f"Context {name} does not exist!")

        repo_contexts = [c for c in repo_contexts if c["name"] != name]
        save_repo_contexts(target_repo, repo_contexts)
        settings.reload()
        rich.print(f"[success]✅  Deleted context {name} from {target_repo}!")


@app.command()
def upload(
        name: str = typer.Argument(
            ..., help="Local context name", autocompletion=context_names
        ),
        repo: Path = typer.Option(
            ..., "-r", "--repo", exists=True, file_okay=True, dir_okay=False,
            autocompletion=context_repo_paths, help="Target context repository"
        ),
):
    """Upload a local context to a repository."""

    if name not in [c["name"] for c in local_contexts]:
        raise typer.BadParameter(f"Context {name} does not exist in local configuration!")

    repo_list = list(settings.get("context_repositories") or [])
    if str(repo) not in repo_list:
        raise typer.BadParameter(f"Context repository {repo} is not configured!")

    contexts = load_repo_contexts(repo)
    if any(c["name"] == name for c in contexts):
        raise typer.BadParameter(
            f"Context {name} already exists in repository {repo}!"
        )

    context = next(c for c in local_contexts if c["name"] == name)
    contexts.append(context)
    save_repo_contexts(repo, contexts)
    settings.reload()
    rich.print(f"[success]✅  Uploaded context {name} to {repo}!")


@app.command("list")
def list_contexts():
    """List all contexts and their locations."""

    repo_list = list(settings.get("context_repositories") or [])
    ctx_map: dict[str, list[str]] = {}

    for repo in repo_list:
        repo_path = Path(repo)
        for ctx in load_repo_contexts(repo_path):
            ctx_map.setdefault(ctx["name"], []).append(str(repo_path))

    for ctx in local_contexts:
        ctx_map.setdefault(ctx["name"], []).append(str(config_path))

    table = Table("Context", "Locations")
    for name, sources in sorted(ctx_map.items()):
        precedence = sources[-1]
        disp = [f"{src}{' *' if src == precedence else ''}" for src in sources]
        table.add_row(name, ", ".join(disp))

    rich.print(table)
