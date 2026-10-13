import json
from pathlib import Path

import rich
import typer
from kubernetes.client import AppsV1Api
from rich.prompt import Confirm
from rich.table import Table

from solradm.commands.kube.common import _ensure_state_dir
from solradm.commands.kube.subapp import app
from solradm.config.context import Context
from solradm.config.util import get_current_context
from solradm.kube.utils import get_kube_context_info

@app.command(help="Restore replicas from a saved state file")
def resume(
        kubecontext: str | None = typer.Option(None, "--kubecontext", "-k", help="Kubecontext name to use (defaults to current context)"),
        state_file: Path | None = typer.Option(None, "--state-file", help="State file to load", dir_okay=False),
) -> None:
    """Scale previously suspended workloads back to their original replicas."""
    if kubecontext is None:
        current = get_current_context()
        kubecontext = current.kubecontext
        if kubecontext is None:
            rich.print("[error] ❌ No kubecontext specified and current context has no kubecontext configured")
            raise typer.Exit(1)
        kube = get_kube_context_info(current)
    else:
        kube = get_kube_context_info(Context(name=None, zk="", kubecontext=kubecontext))

    sf = state_file or (_ensure_state_dir() / f"{kubecontext}.json")
    if not sf.exists():
        rich.print(f"[error] ❌ State file {sf} does not exist")
        raise typer.Exit(1)

    with open(sf) as f:
        data = json.load(f)

    deployments = data.get("deployments", {})
    statefulsets = data.get("statefulsets", {})

    if not deployments and not statefulsets:
        rich.print("[error] ❌ State file contains no workloads")
        raise typer.Exit(1)

    table = Table(title="Workloads", header_style="bold magenta")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Replicas", justify="right")
    for name, replicas in deployments.items():
        table.add_row("Deployment", name, str(replicas))
    for name, replicas in statefulsets.items():
        table.add_row("StatefulSet", name, str(replicas))
    rich.print(table)

    if not Confirm.ask("Proceed with restoring these workloads?"):
        raise typer.Exit(0)

    api = AppsV1Api(kube.api_client)
    for name, replicas in deployments.items():
        api.patch_namespaced_deployment_scale(name, kube.namespace, {"spec": {"replicas": replicas}})
    for name, replicas in statefulsets.items():
        api.patch_namespaced_stateful_set_scale(name, kube.namespace, {"spec": {"replicas": replicas}})

    sf.unlink(missing_ok=True)

    rich.print(f"[success]✅  Restored workloads for kubecontext '{kubecontext}' from {sf} and removed the saved state")
