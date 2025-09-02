import os.path
from pathlib import Path

import rich.console
import yaml
from dynaconf import Dynaconf
from platformdirs import user_config_dir
from rich.console import Console
from rich.theme import Theme
from solradm.config.util import is_valid_context_repo

config_path = Path(os.path.join(user_config_dir("solradm", "eclipse"), "settings.yaml"))

_existing_config: dict = {}
if config_path.exists():
    with open(config_path) as f:
        _existing_config = yaml.safe_load(f) or {}

local_contexts: list = _existing_config.get("contexts", {}).get("available", []).copy()
context_repositories: list[str] = _existing_config.get("context_repositories", [])


def persist(repos_override=None):
    """Persist solradm configuration to the appdata settings file.

    Only local contexts and other configuration stored in appdata are
    written back. Contexts coming from external context repositories are
    not persisted here.
    """

    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {}
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    data["contexts"] = {
        "available": local_contexts,
        "current": settings.contexts.current.to_dict(),
    }

    if settings.get("auth"):
        auth = settings.auth
        data["auth"] = auth.to_dict() if hasattr(auth, "to_dict") else auth
    if settings.get("config_dir"):
        data["config_dir"] = str(settings.config_dir)

    repos = settings.get("context_repositories") or []
    data["context_repositories"] = list(repos_override if repos_override is not None else repos)

    with open(config_path, "w") as f:
        f.write(yaml.safe_dump(data, sort_keys=False))


settings = Dynaconf(
    envvar_prefix="DYNACONF",
    settings_files=context_repositories + [config_path],
    load_dotenv=True,
    merge_enabled=True,
)

theme = Theme({
    "text": "cyan",
    "question": "bold green",
    "success": "green",
    "warning": "yellow",
    "error": "red",
})

console = Console(style="text", theme=theme)

rich._console = console

if not os.path.exists(config_path):
    rich.print(
        """This is your first time running [red bold]solradm[/]!
[magenta]Before proceeding, it is highly recommended to run eclipse-setup.bat so all the supporting tools and configurations are installed on your machine. See the setup section in the documentation website for instructions[/].

Interacting with Solr and ZooKeeper through solradm involves [blue][bold]contexts[/bold][/blue]. They are similar to oc/kubectl contexts, if you are familiar with them.
Essentially, a context is a named environment, linked to a specific ZooKeeper address. You can save new contexts to your local machine, switch between them,
share them, create temporary ones, and so on...
"""
    )

    from rich.prompt import Confirm, Prompt

    config_path.parent.mkdir(parents=True, exist_ok=True)

    create_ctx = Confirm.ask(
        "Would you like to set up an initial context?", default=True
    )

    contexts_avail: list[dict] = []
    current_context: dict = {}

    if create_ctx:
        from solradm.config.interactive import setup_context

        new_context = setup_context.setup()
        contexts_avail.append(new_context.as_dict())
        current_context = {"name": new_context.name}

    create_repo = Confirm.ask(
        "Would you like to add a context repository?", default=False
    )

    if not create_ctx and not create_repo:
        rich.print(
            "[error]You must set up at least an initial context or a context repository."
        )
        exit(1)

    settings.set("contexts", {"available": contexts_avail, "current": current_context})

    repo_path = None
    if create_repo:
        repo_path_str = Prompt.ask("Path to context repository")
        repo_path = Path(repo_path_str)
        if not is_valid_context_repo(repo_path):
            rich.print(
                f"[error]Context repository {repo_path} is invalid."
            )
            exit(1)
        repo_str = str(repo_path)
        settings.set("context_repositories", [repo_str])
        context_repositories.append(repo_str)
        settings.configure(settings_files=context_repositories + [config_path])
    else:
        settings.set("context_repositories", [])

    local_contexts[:] = contexts_avail

    rich.print(
        """Great! Now we need to set-up authentication to your cluster.
Authentication to Solr clusters in solradm is not tied a specific context. You set your username and password globally, and all contexts will use it. This also means that
all clusters that you interact with must have your credentials registered. Note that you can arbitrarily change login info using [italic]solradm auth[/].

Please use your personal Solr administration token."""
    )
    from solradm.config.interactive import setup_solrauth

    auth = setup_solrauth.setup()
    settings.set("auth", {"user": auth.login, "password": auth.password})

    from solradm.config.interactive import setup_config_dir

    config_dir = setup_config_dir.setup()
    settings.set("config_dir", str(config_dir))

    persist()

    parts = []
    if create_ctx:
        parts.append(f"a new solradm context named [red]{new_context.name}[/]")
    parts.append(f"authentication using the [red]{auth.login}[/] token")
    parts.append(f"a default configuration directory at [red]{config_dir}[/]")
    rich.print("Great! You have set-up " + ", ".join(parts) + "!")
    if repo_path:
        rich.print(f"Added context repository at [red]{repo_path}[/]")

    rich.print(
        "There are many commands to manage contexts. Type [purple italic]solradm context --help[/] to see them all."
    )

    exit(0)

