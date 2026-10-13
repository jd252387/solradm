import json
import re
from pathlib import Path

import rich
import typer
from kubernetes.client import AppsV1Api
from rich.prompt import Confirm

from solradm.commands.kube.common import (
    _ensure_state_dir,
    _get_workloads_by_labels,
    _get_workloads_by_pattern,
    _print_workloads,
)
from solradm.commands.kube.subapp import app
from solradm.completion.kube import workload_names
from solradm.config.context import Context
from solradm.config.util import get_current_context
from solradm.kube.utils import get_kube_context_info

@app.command(help="Scale workloads matching a regex down to zero and save their replicas")
def suspend(
        kubecontext: str | None = typer.Option(None, "--kubecontext", "-k", help="Kubecontext name to use (defaults to current context)"),
        pattern: str | None = typer.Option(None, "--pattern", "-p", help="Regex for deployment/statefulset names",
                                           autocompletion=workload_names),
        label: list[str] | None = typer.Option(None, "--label", "-l", help="Label selector for deployment/statefulsets (can be specified multiple times)"),
        state_file: Path | None = typer.Option(None, "--state-file", help="File to store replica state", dir_okay=False),
        dry: bool = typer.Option(False, "--dry", help="Save state without scaling workloads"),
) -> None:
    """Scale matching deployments and statefulsets to zero replicas."""
    if bool(pattern) == bool(label):
        rich.print("[error] ❌ Exactly one of --pattern/-p or --label/-l must be specified")
        raise typer.Exit(1)

    if kubecontext is None:
        current = get_current_context()
        kubecontext = current.kubecontext
        if kubecontext is None:
            rich.print("[error] ❌ No kubecontext specified and current context has no kubecontext configured")
            raise typer.Exit(1)
    kube = get_kube_context_info(Context(name=None, zk="", kubecontext=kubecontext))

    sf = state_file or (_ensure_state_dir() / f"{kubecontext}.json")
    if sf.exists():
        if not Confirm.ask(f"[warning]⚠️ A saved state already exists for kubecontext '{kubecontext}' at {sf}. Are you sure you would like to overwrite it?"):
            raise typer.Exit(1)

    if pattern is not None:
        workloads = _get_workloads_by_pattern(kube, re.compile(pattern))
    else:
        workloads = _get_workloads_by_labels(kube, label)

    deployments, statefulsets = workloads
    if not deployments and not statefulsets:
        rich.print("[error] ❌ No deployments or statefulsets match the given pattern")
        raise typer.Exit(1)

    _print_workloads(deployments, statefulsets)
    action = "saving state (dry run; no scaling)" if dry else "scaling these workloads to zero"
    if not Confirm.ask(f"Proceed with {action}?"):
        raise typer.Exit(0)

    sf.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "deployments": {d.metadata.name: d.spec.replicas for d in deployments},
        "statefulsets": {s.metadata.name: s.spec.replicas for s in statefulsets},
    }
    with open(sf, "w") as f:
        json.dump(data, f)

    if dry:
        rich.print(f"[success]✅  Saved workload state for kubecontext '{kubecontext}' to {sf} (no scaling performed)")
        return

    api = AppsV1Api(kube.api_client)
    for d in deployments:
        api.patch_namespaced_deployment_scale(d.metadata.name, kube.namespace, {"spec": {"replicas": 0}})
    for s in statefulsets:
        api.patch_namespaced_stateful_set_scale(s.metadata.name, kube.namespace, {"spec": {"replicas": 0}})

    rich.print(f"[success]✅  Scaled workloads for kubecontext '{kubecontext}' and saved state to {sf}")
