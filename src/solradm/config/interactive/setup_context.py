from typing import Dict

import rich.console
from kubernetes.config import list_kube_config_contexts
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from solradm.config.util import Context
from solradm.kube.utils import get_kubecontext


def setup() -> Context:
    context_name = ""
    while context_name == "":
        context_name = Prompt.ask("[question]Enter your initial context name -> ")
    zk_address = ""
    while zk_address == "":
        zk_address = Prompt.ask(
            "[question]Enter your context's ZooKeeper address. An address should be in the form of [underline]{host}:{port}[/], e.g. [italic]localhost:2181[/] ->"
        )

    new_context = Context(context_name, zk_address)
    should_setup_openshift = Confirm.ask(f"""[text]Some actions using solradm also require a corresponding [purple italic]kubecontext[/].
A kubecontext is a mapping between a name, and a corresponding Kubernetes cluster + namespace + user. For example, the eclipse-setup.bat script sets up multiple kubecontexts, such as solrz9, solrm9, and solrp9, that map to a namespace within each OpenShift cluster, using a dedicated ServiceAccount (which is named \"kubecontext-admin\" and found in the production namespace within each production environment).
Using kubecontexts, you can quickly switch Kubernetes environments on your machine using kubectx (which is also installed by eclipse-setup.bat). 

Solradm actions that require a kubecontext have 2 modes - 
- If a certain solradm context also specifies a target kubecontext, that context will be used. 
- If the solradm context [underline]does not[/] specify a kubecontext, solradm can opt to use the currently active context on your machine (the one displayed when you type [italic]oc project[/]. Note that this option requires a confirmation flag in commands.

[question]Would you like to setup a kubecontext for {context_name}?
""")
    if should_setup_openshift:
        rich.print("Fetching kubecontexts...")
        contexts, active_context = list_kube_config_contexts()
        table = Table(
            title="Kubernetes Contexts",
            header_style="bold cyan",
            style="white bold",
            row_styles=["white bold"],
            title_style="magenta bold",
        )
        table.add_column("Name", style="bold")
        table.add_column("Active", style="green", justify="center")
        for ctx in contexts:
            name = ctx["name"]
            if ctx == active_context:
                table.add_row(Text(name, style="bold green"), "✅")
            else:
                table.add_row(name, "")
        rich.print(table)

        kubecontext = Prompt.ask("[question]Enter the target kubecontext -> \n")
        while not get_kubecontext(kubecontext):
            kubecontext = Prompt.ask(
                f'[question]"{kubecontext}" is not an available kubecontext! Choose an available kubecontext, or add a new kubecontext and try again... -> \n'
            )

        new_context.kubecontext = kubecontext

    return new_context