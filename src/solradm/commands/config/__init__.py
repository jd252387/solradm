import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import rich
import typer
import yaml
from kazoo.handlers.threading import KazooTimeoutError
from kubernetes.client import CoreV1Api
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from typer import Typer
from typer.models import OptionInfo

from solradm.commands.callbacks import add_verbosity_option
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
from solradm.kube.utils import get_kube_context_info, get_kubecontext
from solradm.zk import get_client

app = Typer()
add_verbosity_option(app)
repo_app = Typer(help="Manage context repositories.")
app.add_typer(repo_app, name="repo")


def _to_dict(obj):
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, list):
        return [_to_dict(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def _ensure_kubecontext_exists(kubecontext: str):
    if not get_kubecontext(kubecontext):
        raise typer.BadParameter(f"Kubecontext {kubecontext} does not exist!")


def _require_namespace(namespace) -> str:
    value = _coerce_optional_option(namespace)
    if value is None:
        raise typer.BadParameter(
            "A namespace must be provided when specifying a kubecontext."
        )

    trimmed = value.strip()
    if not trimmed:
        raise typer.BadParameter("Namespace cannot be empty")

    return trimmed


def _coerce_optional_option(value):
    return value.default if isinstance(value, OptionInfo) else value


def _format_context_line(label: str, value: str) -> str:
    return f"[bold]{label}:[/] {value}"


def _get_repo_list() -> list[dict[str, str]]:
    repos = settings.get("context_repositories") or []
    normalized: list[dict[str, str]] = []
    for repo in repos:
        if isinstance(repo, dict):
            name = repo.get("name")
            path = repo.get("path")
        else:
            name = getattr(repo, "name", None)
            path = getattr(repo, "path", None)

        if not name or not path:
            continue
        normalized.append({"name": str(name), "path": str(path)})

    return normalized


def _repo_paths(repo_list: list[dict[str, str]]) -> list[str]:
    return [repo["path"] for repo in repo_list]


def _find_repo_by_name(name: str, repo_list: list[dict[str, str]]):
    for repo in repo_list:
        if repo["name"] == name:
            return repo
    return None


def print_current_context(*, console: Console | None = None):
    """Render the active context in a human-friendly format."""

    ctx = get_current_context()
    console = console or Console()
    name_display = ctx.name if ctx.name is not None else "Temporary (unsaved)"

    kubecontext = ctx.kubecontext or "-"
    namespace = ctx.namespace or "-"

    console.print(
        "\n".join(
            [
                "[bold]Current context[/]",
                _format_context_line("Name", name_display),
                _format_context_line("ZooKeeper", ctx.zk),
                _format_context_line("Kubecontext", kubecontext),
                _format_context_line("Namespace", namespace),
            ]
        )
    )


@app.command()
def current():
    """Show the currently active context."""

    print_current_context()


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


verify_zk_connection = _verify_zk_connection


@app.command()
def switch(
        name: str = typer.Argument(
            ..., help="Context name", autocompletion=context_names
        )
):
    """Switch to an existing context."""

    if name in [context.name for context in settings.contexts.available]:
        settings.contexts.current = {"name": name}
        if _verify_zk_connection():
            persist()
            if name in [c["name"] for c in local_contexts]:
                location = "local configuration"
            else:
                repo_list = _get_repo_list()
                location = "unknown location"
                for repo in reversed(repo_list):
                    repo_path = Path(repo["path"])
                    contexts = load_repo_contexts(repo_path)
                    if any(c["name"] == name for c in contexts):
                        location = f"repository {repo['name']} ({repo_path})"
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


@repo_app.command("create")
def create_repo(
        name: str = typer.Argument(..., help="Repository name"),
        path: Path = typer.Argument(
            ..., exists=False, file_okay=True, dir_okay=False, resolve_path=True,
            help="Path to new context repository",
        ),
):
    """Create a new context repository."""

    repo_list = _get_repo_list()
    name = name.strip()
    if not name:
        raise typer.BadParameter("Repository name cannot be empty")
    if any(repo["name"] == name for repo in repo_list):
        raise typer.BadParameter(f"Context repository named {name} already exists!")

    if any(repo["path"] == str(path) for repo in repo_list):
        raise typer.BadParameter(f"Context repository {path} already exists!")

    if path.exists():
        raise typer.BadParameter(f"Context repository {path} already exists!")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump({"contexts": {"available": []}}, f, sort_keys=False)

    repo_entry = {"name": name, "path": str(path)}
    repo_list.append(repo_entry)
    settings.context_repositories = repo_list
    persist(repo_list)
    settings.configure(settings_files=_repo_paths(repo_list) + [config_path])
    settings.reload()
    rich.print(f"[success]✅  Created context repository {name} at {path}!")


@repo_app.command("add")
def add_repo(
        name: str = typer.Argument(..., help="Repository name"),
        path: Path = typer.Argument(
            ..., exists=True, file_okay=True, dir_okay=False, resolve_path=True, help="Path to context repository"
        ),
):
    """Add a new context repository."""

    repo_list = _get_repo_list()
    name = name.strip()
    if not name:
        raise typer.BadParameter("Repository name cannot be empty")

    if any(repo["name"] == name for repo in repo_list):
        raise typer.BadParameter(f"Context repository named {name} already exists!")

    path_str = str(path)
    if any(repo["path"] == path_str for repo in repo_list):
        raise typer.BadParameter(f"Context repository {path} already exists!")

    if not is_valid_context_repo(path):
        raise typer.BadParameter(f"{path} is not a valid context repository")

    repo_list.append({"name": name, "path": path_str})
    settings.context_repositories = repo_list
    persist(repo_list)
    settings.configure(settings_files=_repo_paths(repo_list) + [config_path])
    settings.reload()
    rich.print(f"[success]✅  Added context repository {name} at {path}!")


@repo_app.command("remove")
def remove_repo(
        name: str = typer.Argument(
            ..., autocompletion=context_repo_paths, help="Name of the context repository"
        ),
):
    """Remove a context repository."""

    repo_list = _get_repo_list()
    target_repo = _find_repo_by_name(name, repo_list)
    if not target_repo:
        raise typer.BadParameter(f"Context repository {name} does not exist!")

    repo_list = [repo for repo in repo_list if repo["name"] != name]
    settings.context_repositories = repo_list
    persist(repo_list)
    settings.configure(settings_files=_repo_paths(repo_list) + [config_path])
    settings.reload()
    rich.print(
        f"[success]✅  Deleted context repository {name} ({target_repo['path']})!"
    )


@repo_app.command("list")
def list_repos():
    """List configured context repositories and their contexts."""

    repo_list = _get_repo_list()
    table = Table("Name", "Repository", "Contexts")
    for repo in repo_list:
        repo_path = Path(repo["path"])
        contexts = [c["name"] for c in load_repo_contexts(repo_path)]
        table.add_row(repo["name"], str(repo_path), ", ".join(contexts) if contexts else "-")
    rich.print(table)


@repo_app.command("open")
def open_repo(
        name: str = typer.Argument(
            ..., autocompletion=context_repo_paths, help="Name of the context repository"
        ),
):
    """Open the location of a configured context repository."""

    repo_list = _get_repo_list()
    target_repo = _find_repo_by_name(name, repo_list)
    if not target_repo:
        raise typer.BadParameter(f"Context repository {name} is not configured!")

    path = Path(target_repo["path"])
    if sys.platform.startswith("win"):
        subprocess.run(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)])
    else:
        subprocess.run(["xdg-open", str(path.parent)])


@repo_app.command("upload")
def upload(
        name: str = typer.Argument(
            ..., help="Local context name", autocompletion=context_names
        ),
        repo: str = typer.Option(
            ..., "-r", "--repo", autocompletion=context_repo_paths, help="Target context repository name",
        ),
):
    """Upload a local context to a repository."""

    if name not in [c["name"] for c in local_contexts]:
        raise typer.BadParameter(f"Context {name} does not exist in local configuration!")

    repo_list = _get_repo_list()
    target_repo = _find_repo_by_name(repo, repo_list)
    if not target_repo:
        raise typer.BadParameter(f"Context repository {repo} is not configured!")

    repo_path = Path(target_repo["path"])
    contexts = load_repo_contexts(repo_path)
    if any(c["name"] == name for c in contexts):
        raise typer.BadParameter(
            f"Context {name} already exists in repository {repo}!"
        )

    context = next(c for c in local_contexts if c["name"] == name)
    contexts.append(context)
    save_repo_contexts(repo_path, contexts)
    settings.reload()
    rich.print(f"[success]✅  Uploaded context {name} to {repo}!")

@app.command()
def connect(
        zk: str = typer.Argument(..., help="ZooKeeper Host"),
        kubecontext: str = typer.Option(
            None, help="Kubernetes context", autocompletion=kube_contexts
        ),
        namespace: str | None = typer.Option(
            None,
            "--namespace",
            "-n",
            help="Target namespace for the kubecontext",
        ),
):
    """Temporarily connect to a ZooKeeper host."""

    kubecontext = _coerce_optional_option(kubecontext)
    namespace = _coerce_optional_option(namespace)

    settings.contexts.current = {"zk": zk}

    if namespace and not kubecontext:
        raise typer.BadParameter("--namespace can only be used together with --kubecontext")

    if kubecontext:
        kubecontext = kubecontext.strip()
        if not kubecontext:
            raise typer.BadParameter("Kubecontext cannot be empty")
        _ensure_kubecontext_exists(kubecontext)
        resolved_namespace = _require_namespace(namespace)
        settings.contexts.current["kubecontext"] = kubecontext
        settings.contexts.current["namespace"] = resolved_namespace

    if _verify_zk_connection():
        persist()
        rich.print(
            "Switched to temporary context. Use [italic]context persist[/] to save the context permanently."
        )


@app.command()
def connect_current():
    """Connect using the configured kubecontext and NodePort service."""

    kube = get_kube_context_info()

    services = CoreV1Api(kube.api_client).list_namespaced_service(kube.namespace).items
    zk_svc = next(
        (svc for svc in services if "zk-nodeport" in svc.metadata.name),
        None,
    )

    if not zk_svc or not zk_svc.spec.ports:
        raise typer.BadParameter(
            'Could not find service with "zk-nodeport" in current namespace'
        )

    node_port = zk_svc.spec.ports[0].node_port
    api_host = urlparse(kube.api_client.configuration.host).hostname
    if not api_host:
        raise typer.BadParameter("Unable to determine API server host")

    zk_address = f"{api_host}:{node_port}"
    connect(zk_address, kube.name, namespace=kube.namespace)


@app.command()
def save(name: str = typer.Argument(..., help="Context name")):
    """Persist the current temporary context under a new name."""

    if "name" not in settings.contexts.current:
        add(
            name,
            settings.contexts.current.zk,
            settings.contexts.current.get("kubecontext"),
            namespace=settings.contexts.current.get("namespace"),
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
        namespace: str | None = typer.Option(
            None,
            "--namespace",
            "-n",
            help="Namespace to associate with the kubecontext",
        ),
        interactive: bool = typer.Option(True, help="Interactive setup mode"),
):
    """Add a new named context."""
    zk = _coerce_optional_option(zk)
    kubecontext = _coerce_optional_option(kubecontext)
    namespace = _coerce_optional_option(namespace)
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
            raise typer.BadParameter(
                "You must specify both a name and a ZooKeeper address! Alternatively, use --interactive to enter interactive setup mode.")
        if name in [context.name for context in settings.contexts.available]:
            raise typer.BadParameter(f"Context {name} already exists!")
        if namespace and not kubecontext:
            raise typer.BadParameter("--namespace can only be used together with --kubecontext")
        resolved_namespace = None
        if kubecontext:
            kubecontext = kubecontext.strip()
            if not kubecontext:
                raise typer.BadParameter("Kubecontext cannot be empty")
            _ensure_kubecontext_exists(kubecontext)
            resolved_namespace = _require_namespace(namespace)
        context = Context(name=name, zk=zk, kubecontext=kubecontext, namespace=resolved_namespace)

    settings.contexts.available = settings.contexts.available + [context.as_dict()]
    local_contexts.append(context.as_dict())
    persist()
    rich.print(f"[success]✅ Added new context {context.name}!")


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
        namespace: str | None = typer.Option(
            None,
            "--namespace",
            "-n",
            help="Namespace associated with the kubecontext",
        ),
):
    """Modify an existing context."""
    zk = _coerce_optional_option(zk)
    kubecontext = _coerce_optional_option(kubecontext)
    namespace = _coerce_optional_option(namespace)

    if zk is None and kubecontext is None and namespace is None:
        raise typer.BadParameter("Please specify --zk, --kubecontext or --namespace")

    if name in [c["name"] for c in local_contexts]:
        for context in settings.contexts.available:
            if context.name == name:
                existing_kubecontext = context.get("kubecontext")
                existing_namespace = context.get("namespace")
                if kubecontext is not None:
                    new_kubecontext = kubecontext
                    if not new_kubecontext or not new_kubecontext.strip():
                        raise typer.BadParameter("Kubecontext cannot be empty")
                    new_kubecontext = new_kubecontext.strip()
                    _ensure_kubecontext_exists(new_kubecontext)
                    new_namespace = _require_namespace(namespace)
                else:
                    new_kubecontext = existing_kubecontext
                    if namespace is not None:
                        if not existing_kubecontext:
                            raise typer.BadParameter(
                                "Cannot set a namespace when the context has no kubecontext configured."
                            )
                        new_namespace = _require_namespace(namespace)
                    else:
                        new_namespace = existing_namespace
                new_context = Context(
                    name,
                    zk=zk if zk else context.zk,
                    kubecontext=new_kubecontext,
                    namespace=new_namespace,
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
        repo_list = _get_repo_list()
        target_repo = None
        repo_contexts = None
        for repo in reversed(repo_list):
            repo_path = Path(repo["path"])
            contexts = load_repo_contexts(repo_path)
            if any(c["name"] == name for c in contexts):
                target_repo = repo
                repo_contexts = contexts
                break
        if not target_repo:
            raise typer.BadParameter(f"Context {name} does not exist!")

        existing = next(c for c in repo_contexts if c["name"] == name)
        existing_kubecontext = existing.get("kubecontext")
        existing_namespace = existing.get("namespace")
        if kubecontext is not None:
            new_kubecontext = kubecontext
            if not new_kubecontext or not new_kubecontext.strip():
                raise typer.BadParameter("Kubecontext cannot be empty")
            new_kubecontext = new_kubecontext.strip()
            _ensure_kubecontext_exists(new_kubecontext)
            new_namespace = _require_namespace(namespace)
        else:
            new_kubecontext = existing_kubecontext
            if namespace is not None:
                if not existing_kubecontext:
                    raise typer.BadParameter(
                        "Cannot set a namespace when the context has no kubecontext configured."
                    )
                new_namespace = _require_namespace(namespace)
            else:
                new_namespace = existing_namespace
        new_context = Context(
            name,
            zk=zk if zk else existing["zk"],
            kubecontext=new_kubecontext,
            namespace=new_namespace,
        )
        repo_contexts = [
            c if c["name"] != name else new_context.as_dict() for c in repo_contexts
        ]
        save_repo_contexts(Path(target_repo["path"]), repo_contexts)
        settings.reload()
        rich.print(
            f"[success]✅  Updated context {name} in {target_repo['name']} ({target_repo['path']})!"
        )


@app.command("remove")
def remove(
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
        repo_list = _get_repo_list()
        target_repo = None
        repo_contexts = None
        for repo in reversed(repo_list):
            repo_path = Path(repo["path"])
            contexts = load_repo_contexts(repo_path)
            if any(c["name"] == name for c in contexts):
                target_repo = repo
                repo_contexts = contexts
                break
        if not target_repo:
            raise typer.BadParameter(f"Context {name} does not exist!")

        repo_contexts = [c for c in repo_contexts if c["name"] != name]
        save_repo_contexts(Path(target_repo["path"]), repo_contexts)
        settings.reload()
        rich.print(
            f"[success]✅  Deleted context {name} from {target_repo['name']} ({target_repo['path']})!"
        )


@app.command("list")
def list_contexts():
    """List all contexts and their locations."""

    repo_list = _get_repo_list()
    ctx_map: dict[str, list[str]] = {}

    for repo in repo_list:
        repo_path = Path(repo["path"])
        for ctx in load_repo_contexts(repo_path):
            ctx_map.setdefault(ctx["name"], []).append(f"{repo['name']} ({repo_path})")

    for ctx in local_contexts:
        ctx_map.setdefault(ctx["name"], []).append(str(config_path))

    table = Table("Context", "Locations")
    for name, sources in sorted(ctx_map.items()):
        precedence = sources[-1]
        disp = [f"{src}{' *' if src == precedence else ''}" for src in sources]
        table.add_row(name, ", ".join(disp))

    rich.print(table)
