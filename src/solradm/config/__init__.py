import os
import os.path
from pathlib import Path

import yaml
from dynaconf import Dynaconf
from platformdirs import user_config_dir
from solradm.lazy import lazy_module

rich = lazy_module("rich")
Console = lazy_module("rich.console").Console
Theme = lazy_module("rich.theme").Theme

config_path = Path(os.path.join(user_config_dir("solradm", "eclipse"), "settings.yaml"))

# Detect if the CLI was invoked for shell completion. When completing we want to
# avoid any interactive prompts that would block and slow down the shell.
IS_COMPLETING = any(key.endswith("_COMPLETE") for key in os.environ)

def persist():
    with open(config_path, "w") as f:
        f.write(yaml.safe_dump(settings.as_dict(), sort_keys=False))


settings = Dynaconf(
    envvar_prefix="DYNACONF",
    settings_files=[config_path],
    load_dotenv=True,
)

theme = Theme({"text": "cyan", "question": "bold green", "success": "green", "warning": "yellow", "error": "red" })

console = Console(style="text", theme=theme)

rich._console = console

# Skip interactive configuration when running shell completion as it would
# otherwise block completion for first-time users.
if not os.path.exists(config_path) and not IS_COMPLETING:
    rich.print("""This is your first time running [red bold]solradm[/]!
[magenta]Before proceeding, it is highly recommended to run eclipse-setup.bat so all the supporting tools and configurations are installed on your machine. See the setup section in the documentation website for instructions[/].

Interacting with Solr and ZooKeeper through solradm involves [blue][bold]contexts[/bold][/blue]. They are similar to oc/kubectl contexts, if you are familiar with them.
Essentially, a context is a named environment, linked to a specific ZooKeeper address. You can save new contexts to your local machine, switch between them, 
share them, create temporary ones, and so on...

-> To get started, we need to create an initial context for you. [bold]Context names should be short and concise.[/] For example, a production environment like Solr 9 Znif, 
should be named [bold italic]solrz9[/]. """)

    from solradm.config.interactive import setup_context
    config_path.parent.mkdir(parents=True, exist_ok=True)
    new_context = setup_context.setup()
    settings.set(
        "contexts",
        {"available": [new_context.as_dict()], "current": {"name": new_context.name}},
    )

    rich.print("""Great! Now we need to set-up authentication to your cluster. 
Authentication to Solr clusters in solradm is not tied a specific context. You set your username and password globally, and all contexts will use it. This also means that
all clusters that you interact with must have your credentials registered. Note that you can arbitrarily change login info using [italic]solradm auth[/].

Please use your personal Solr administration token.""")
    from solradm.config.interactive import setup_solrauth
    auth = setup_solrauth.setup()
    settings.set("auth", {"user": auth.login, "password": auth.password})

    from solradm.config.interactive import setup_config_dir
    config_dir = setup_config_dir.setup()
    settings.set("config_dir", str(config_dir))

    persist()

    rich.print(
        f"""Great! You have set-up a new solradm context named [red]{new_context.name}[/], authentication using the [red]{auth.login}[/] token,
and a default configuration directory at [red]{config_dir}[/]!
There are many commands to manage contexts. Type [purple italic]solradm context --help[/] to see them all.
[dim italic]* Note - the command you entered was intentionally not ran, since you didn't have a context set-up. Please re-run it.
        """
    )

    exit(0)
