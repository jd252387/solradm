import asyncio
import json
import re
from pathlib import Path
import sys

import typer
from platformdirs import user_config_dir
from async_typer import AsyncTyper
from typing import TYPE_CHECKING
from solradm.lazy import lazy_module

from solradm.kube.utils import (
    get_configured_kubecontext,
    find_pods,
    find_pods_by_node_name,
    get_current_kubecontext_namespace,
    run_command_in_pod,
    switch_current_kubecontext,
)

if TYPE_CHECKING:  # pragma: no cover
    from kubernetes.client import AppsV1Api, CoreV1Api
    from kubernetes.stream import stream

app = AsyncTyper()

STATE_FILE = Path(user_config_dir("solradm", "eclipse")) / "kube-scale-state.json"

rich = lazy_module("rich")
Confirm = lazy_module("rich.prompt").Confirm
Table = lazy_module("rich.table").Table


def _load_kube_config():
    switch_current_kubecontext(get_configured_kubecontext())


def _get_workloads(pattern: re.Pattern):
    from kubernetes.client import AppsV1Api

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


@app.async_command(help="Stream logs for matching pods")
async def logs(
    pattern: str = typer.Argument(..., help="Regex of pod or node name"),
    node: bool = typer.Option(False, "--node", help="Treat pattern as node name"),
    container: str | None = typer.Option(None, "--container", "-c", help="Container name"),
):
    """Stream Kubernetes logs from pods matching PATTERN."""

    pods = find_pods_by_node_name(pattern) if node else find_pods(re.compile(pattern))

    if not pods:
        raise typer.BadParameter("No pods matched the given pattern")

    namespace = get_current_kubecontext_namespace()

    from kubernetes.client import CoreV1Api
    from kubernetes.stream import stream

    for pod in pods:
        resp = stream(
            CoreV1Api().read_namespaced_pod_log,
            pod.metadata.name,
            namespace,
            container=container,
            follow=True,
            _preload_content=False,
        )
        try:
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    out = resp.read_stdout()
                    if out:
                        print(out, end="")
                if resp.peek_stderr():
                    err = resp.read_stderr()
                    if err:
                        print(err, end="", file=sys.stderr)
        finally:
            resp.close()


@app.async_command(help="Show /var/solr disk usage for matching pods")
async def disk(
    pattern: str = typer.Argument(..., help="Regex of pod or node name"),
    node: bool = typer.Option(False, "--node", help="Treat pattern as node name"),
):
    """Display disk usage of /var/solr for pods matching PATTERN."""

    _load_kube_config()
    pods = find_pods_by_node_name(pattern) if node else find_pods(re.compile(pattern))

    if not pods:
        raise typer.BadParameter("No pods matched the given pattern")

    table = Table(title="Disk usage", header_style="bold magenta")
    table.add_column("Pod")
    table.add_column("Size", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Avail", justify="right")
    table.add_column("Use%", justify="right")

    async def _df(pod_name: str):
        output = await asyncio.to_thread(run_command_in_pod, pod_name, "df -h /var/solr")
        return pod_name, output

    results = await asyncio.gather(*(_df(p.metadata.name) for p in pods))

    for pod_name, output in results:
        lines = [l for l in output.strip().splitlines() if l]
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                size, used, avail, pct = parts[1:5]
                table.add_row(pod_name, size, used, avail, pct)
                continue
        table.add_row(pod_name, "-", "-", "-", "-")

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

    from kubernetes.client import AppsV1Api

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

    from kubernetes.client import AppsV1Api

    api = AppsV1Api()
    namespace = get_current_kubecontext_namespace()
    for name, replicas in deployments.items():
        api.patch_namespaced_deployment_scale(name, namespace, {"spec": {"replicas": replicas}})
    for name, replicas in statefulsets.items():
        api.patch_namespaced_stateful_set_scale(name, namespace, {"spec": {"replicas": replicas}})

    rich.print(f"[success]✅  Restored workloads from {sf}")
