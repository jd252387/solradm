import json
import re
from pathlib import Path

import rich
import typer
from kubernetes.client import AppsV1Api
from rich.prompt import Confirm
from rich.table import Table
from platformdirs import user_config_dir

from solradm.kube.utils import (
    get_configured_kubecontext,
    get_current_kubecontext_namespace,
    switch_current_kubecontext,
)

app = typer.Typer()

STATE_FILE = Path(user_config_dir("solradm", "eclipse")) / "kube-scale-state.json"


def _load_kube_config():
    switch_current_kubecontext(get_configured_kubecontext())


def _get_workloads(pattern: re.Pattern):
    namespace = get_current_kubecontext_namespace()
    api = AppsV1Api()
    deployments = [
        d
        for d in api.list_namespaced_deployment(namespace).items
        if pattern.search(d.metadata.name)
    ]
    statefulsets = [
        s
        for s in api.list_namespaced_stateful_set(namespace).items
        if pattern.search(s.metadata.name)
    ]
    return deployments, statefulsets


def _print_workloads(deployments, statefulsets):
    table = Table(title="Workloads", header_style="bold magenta")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Replicas", justify="right")
    for d in deployments:
        table.add_row("Deployment", d.metadata.name, str(d.spec.replicas))
    for s in statefulsets:
        table.add_row("StatefulSet", s.metadata.name, str(s.spec.replicas))
    rich.print(table)


@app.command(help="Scale workloads matching a regex down to zero and save their replicas")
def suspend(
    name_regex: str = typer.Argument(..., help="Regex for deployment/statefulset names"),
    state_file: Path = typer.Option(None, "--state-file", help="File to store replica state", dir_okay=False),
):
    """Scale matching deployments and statefulsets to zero replicas."""

    _load_kube_config()
    pattern = re.compile(name_regex)
    deployments, statefulsets = _get_workloads(pattern)
    if not deployments and not statefulsets:
        rich.print("[error] ❌ No deployments or statefulsets match the given pattern")
        raise typer.Exit(1)

    _print_workloads(deployments, statefulsets)
    if not Confirm.ask("Proceed with scaling these workloads to zero?"):
        raise typer.Exit(0)

    sf = state_file or STATE_FILE
    sf.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "deployments": {d.metadata.name: d.spec.replicas for d in deployments},
        "statefulsets": {s.metadata.name: s.spec.replicas for s in statefulsets},
    }
    with open(sf, "w") as f:
        json.dump(data, f)

    api = AppsV1Api()
    namespace = get_current_kubecontext_namespace()
    for d in deployments:
        api.patch_namespaced_deployment_scale(d.metadata.name, namespace, {"spec": {"replicas": 0}})
    for s in statefulsets:
        api.patch_namespaced_stateful_set_scale(s.metadata.name, namespace, {"spec": {"replicas": 0}})

    rich.print(f"[success]✅  Scaled workloads and saved state to {sf}")


@app.command(help="Restore replicas from a saved state file")
def resume(
    state_file: Path = typer.Option(None, "--state-file", help="State file to load", dir_okay=False),
):
    """Scale previously suspended workloads back to their original replicas."""

    _load_kube_config()
    sf = state_file or STATE_FILE
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

    api = AppsV1Api()
    namespace = get_current_kubecontext_namespace()
    for name, replicas in deployments.items():
        api.patch_namespaced_deployment_scale(name, namespace, {"spec": {"replicas": replicas}})
    for name, replicas in statefulsets.items():
        api.patch_namespaced_stateful_set_scale(name, namespace, {"spec": {"replicas": replicas}})

    rich.print(f"[success]✅  Restored workloads from {sf}")
