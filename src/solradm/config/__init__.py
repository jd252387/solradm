import os.path
from pathlib import Path

import rich.console
import yaml
from dynaconf import Dynaconf
from platformdirs import user_config_dir
from rich.console import Console
from rich.theme import Theme

from solradm.config.util import is_valid_context_repo, load_repo_contexts

config_path = Path(os.path.join(user_config_dir("solradm", "eclipse"), "settings.yaml"))

_existing_config: dict = {}
if config_path.exists():
    with open(config_path) as f:
        _existing_config = yaml.safe_load(f) or {}

local_contexts: list = _existing_config.get("contexts", {}).get("available", []).copy()


def _normalize_context_repositories(repos: list) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    existing_names: set[str] = set()
    for idx, repo in enumerate(repos):
        raw_name = None
        raw_path = None
        if isinstance(repo, str):
            raw_path = repo
        elif isinstance(repo, dict):
            raw_name = repo.get("name")
            raw_path = repo.get("path")
        else:
            raw_name = getattr(repo, "name", None)
            raw_path = getattr(repo, "path", None)

        if raw_path is None:
            continue

        base_name = (raw_name or Path(raw_path).stem or f"repo-{idx + 1}").strip()
        if not base_name:
            base_name = f"repo-{idx + 1}"

        name = base_name
        suffix = 2
        while name in existing_names:
            name = f"{base_name}-{suffix}"
            suffix += 1

        existing_names.add(name)
        normalized.append({"name": name, "path": str(raw_path)})

    return normalized


context_repositories: list[dict[str, str]] = _normalize_context_repositories(
    _existing_config.get("context_repositories", [])
)


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
    if settings.get("artifactory"):
        art = settings.artifactory
        data["artifactory"] = art.to_dict() if hasattr(art, "to_dict") else art
    if settings.get("config_dir"):
        data["config_dir"] = str(settings.config_dir)

    repos = _normalize_context_repositories(settings.get("context_repositories") or [])
    repo_data = repos_override if repos_override is not None else repos
    data["context_repositories"] = list(_normalize_context_repositories(repo_data))

    if settings.get("backup_base_location"):
        data["backup_base_location"] = settings.backup_base_location

    with open(config_path, "w") as f:
        f.write(yaml.safe_dump(data, sort_keys=False))


settings = Dynaconf(
    envvar_prefix="DYNACONF",
    settings_files=[repo["path"] for repo in context_repositories] + [config_path],
    load_dotenv=True,
    merge_enabled=True,
)

settings.set("context_repositories", context_repositories, merge=False)

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
        "[question][underline]Would you like to set up an initial context?[/] [yellow bold italic]If you don't setup an initial context, you can instead just use a shared context repository, which is the recommended way to work in a team! These will be explained later...",
        default=True
    )

    contexts_avail: list[dict] = []
    current_context: dict = {}

    if create_ctx:
        from solradm.config.interactive import setup_context

        context_name = ""
        while context_name == "":
            context_name = Prompt.ask("[question]Enter your initial context name -> ")

        new_context = setup_context.setup(context_name)
        contexts_avail.append(new_context.as_dict())
        current_context = {"name": new_context.name}

    rich.print(
        "[text]Context repositories are files located on your machine or more commonly, on a network drive. These can store additional contexts that you may connect to. If they are stored on a network drive, any user using solradm can edit them, and those changes will be replicated to all other users using the repository. ")
    rich.print(
        "[text bold]Your team most likely has already setup a common repository on a network drive, so it is recommended to ask them for it and set it up here. ")
    rich.print(
        "[yellow bold]Note that it is possible to use a context repository without declaring local contexts, and only use contexts found in repositories.")
    create_repo = Confirm.ask(
        "[question]Would you like to add a context repository?", default=False
    )

    if not create_ctx and not create_repo:
        rich.print(
            "[error]You must set up at least an initial context or a context repository."
        )
        exit(1)

    repo_path = None
    repo_contexts: list[dict] = []
    if create_repo:
        is_valid_repo = False
        while not is_valid_repo:
            repo_path_str = Prompt.ask("Enter the path to context repository -> ")
            repo_path = Path(repo_path_str)
            if not is_valid_context_repo(repo_path):
                rich.print(
                    f"[error]Context repository {repo_path} is invalid!"
                )
            else:
                is_valid_repo = True

        repo_name = Prompt.ask("Enter a unique name for this repository -> ").strip()
        while not repo_name or any(
            repo.get("name") == repo_name for repo in context_repositories
        ):
            repo_name = Prompt.ask(
                "[error]Repository name must be unique and non-empty. Enter again -> "
            ).strip()

        repo_entry = {"name": repo_name, "path": str(repo_path)}
        context_repositories.append(repo_entry)
        settings.configure(
            settings_files=[repo["path"] for repo in context_repositories]
            + [config_path]
        )
        repo_contexts = load_repo_contexts(repo_path)
        settings.set("context_repositories", context_repositories)
    else:
        settings.set("context_repositories", [])

    all_contexts = contexts_avail + repo_contexts
    if not create_ctx and repo_contexts:
        current_context = {"name": repo_contexts[0]["name"]}
    settings.set("contexts", {"available": all_contexts, "current": current_context}, merge=False)

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

    if Confirm.ask(
        "[question]Would you like to set up Artifactory access for the [italic]rt[/] commands? (optional)",
        default=False,
    ):
        from solradm.config.interactive import setup_artifactory

        settings.set("artifactory", setup_artifactory.setup())

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
