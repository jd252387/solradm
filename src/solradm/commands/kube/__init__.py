import asyncio
import json
import re
import webbrowser
from itertools import cycle
from pathlib import Path

import rich
import typer
import urllib3
from async_typer import AsyncTyper
from kubernetes import client
from kubernetes.client import ApiException
from kubernetes.client import AppsV1Api
from kubernetes.client import CoreV1Api
from kubernetes.client import CustomObjectsApi
from platformdirs import user_config_dir
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from solradm.commands.callbacks import add_verbosity_option
from solradm.completion.kube import pod_names, container_names, workload_names
from solradm.completion.nodes import node_names
from solradm.kube.utils import (
    find_pods,
    find_pods_by_node_name,
    get_kube_context_info,
    run_command_in_pod,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = AsyncTyper()
add_verbosity_option(app)

STATE_DIR = Path(user_config_dir("solradm", "eclipse")) / "kube-scale-state"


def _ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


def _state_file_for_context(context_name: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", context_name)
    return _ensure_state_dir() / f"{safe_name}.json"

def is_openshift_cluster(kube) -> bool:
    try:
        groups = client.ApisApi(api_client=kube.api_client).get_api_versions().groups

        for group in groups:
            if group.name == "route.openshift.io":
                return True
    except ApiException as e:
        rich.print(f"[error] ❌ ApiExtensions request failed: {e}")
        return False

    return False

def _get_workloads(kube, pattern: re.Pattern, namespace: str | None = None):
    namespace = namespace or kube.namespace
    api = AppsV1Api(kube.api_client)
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
        pattern: str = typer.Argument(..., help="Regex of pod or node name", autocompletion=pod_names),
        node: bool = typer.Option(False, "--node", help="Treat pattern as node name", autocompletion=node_names),
        container: str | None = typer.Option(None, "--container", "-c", help="Container name",
                                             autocompletion=container_names),
):
    """Stream Kubernetes logs from pods matching PATTERN."""
    kube = get_kube_context_info()
    pods = (
        find_pods_by_node_name(kube, pattern)
        if node
        else find_pods(kube, re.compile(pattern))
    )

    if not pods:
        raise typer.BadParameter("No pods matched the given pattern")

    namespace = kube.namespace

    console = Console()
    color_cycle = cycle(["red", "green", "yellow", "blue", "magenta", "cyan"])
    pod_colors = {p.metadata.name: next(color_cycle) for p in pods}

    def _stream_logs(pod_name: str):
        resp = CoreV1Api(kube.api_client).read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            follow=True,
            _preload_content=False,
        )
        for line in resp.stream():
            console.print(f"[{pod_colors[pod_name]}]{pod_name}[/] {line.decode().rstrip()}")

    await asyncio.gather(
        *(asyncio.to_thread(_stream_logs, p.metadata.name) for p in pods)
    )


@app.async_command(help="Show /var/solr/data disk usage for matching pods")
async def disk(
        pattern: str = typer.Argument(..., help="Regex of pod or node name", autocompletion=pod_names),
        node: bool = typer.Option(False, "--node", help="Treat pattern as node name"),
        ascending: bool = typer.Option(False, "--ascending", "-a", help="Sort ascending by used space"),
):
    """Display disk usage of /var/solr for pods matching PATTERN."""

    kube = get_kube_context_info()
    pods = (
        find_pods_by_node_name(kube, pattern)
        if node
        else find_pods(kube, re.compile(pattern))
    )

    if not pods:
        raise typer.BadParameter("No pods matched the given pattern")

    table = Table(title="Disk usage", header_style="bold magenta")
    table.add_column("Pod")
    table.add_column("Size", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Avail", justify="right")
    table.add_column("Use%", justify="right")

    async def _df(pod_name: str):
        output = await asyncio.to_thread(run_command_in_pod, kube, pod_name, "df -h /var/solr/data")
        return pod_name, output

    results = await asyncio.gather(*(_df(p.metadata.name) for p in pods))

    def _parse_size(s: str) -> float:
        units = {"K": 1, "M": 2, "G": 3, "T": 4, "P": 5}
        try:
            num = float(re.sub("[^0-9.]+", "", s))
            unit = re.sub("[0-9.]+", "", s).strip().upper()[:1]
            return num * (1024 ** units.get(unit, 0))
        except Exception:
            return 0

    rows = []
    for pod_name, output in results:
        lines = [l for l in output.strip().splitlines() if l]
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                size, used, avail, pct = parts[1:5]
                rows.append((pod_name, size, used, avail, pct, _parse_size(used)))
                continue
        rows.append((pod_name, "-", "-", "-", "-", 0))

    rows.sort(key=lambda r: r[5], reverse=not ascending)
    for pod_name, size, used, avail, pct, _ in rows:
        table.add_row(pod_name, size, used, avail, pct)

    rich.print(table)


@app.command(help="Scale workloads matching a regex down to zero and save their replicas")
def suspend(
        kubecontext: str = typer.Option(..., "--kubecontext", "-k", help="Kubecontext name to use"),
        name_regex: str = typer.Argument(..., help="Regex for deployment/statefulset names",
                                         autocompletion=workload_names),
        state_file: Path = typer.Option(None, "--state-file", help="File to store replica state", dir_okay=False),
        dry: bool = typer.Option(False, "--dry", help="Save state without scaling workloads"),
):
    """Scale matching deployments and statefulsets to zero replicas."""
    kube = get_kube_context_info(kubecontext=kubecontext)

    sf = state_file or _state_file_for_context(kubecontext)
    if sf.exists():
        rich.print(f"[error] ❌ A saved state already exists for kubecontext '{kubecontext}' at {sf}")
        raise typer.Exit(1)

    pattern = re.compile(name_regex)
    deployments, statefulsets = _get_workloads(kube, pattern)
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


@app.command(help="Restore replicas from a saved state file")
def resume(
        kubecontext: str = typer.Option(..., "--kubecontext", "-k", help="Kubecontext name to use"),
        state_file: Path = typer.Option(None, "--state-file", help="State file to load", dir_okay=False),
):
    """Scale previously suspended workloads back to their original replicas."""

    kube = get_kube_context_info(kubecontext=kubecontext)

    sf = state_file or _state_file_for_context(kubecontext)
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


@app.command(help="Open the directory that stores saved kube workload states")
def dir():
    """Open the directory containing saved kube workload state files."""

    directory = _ensure_state_dir()
    typer.launch(str(directory))
    rich.print(f"[success]✅  Opened kube state directory at {directory}")


@app.command(help="Open OpenShift console for the current namespace")
def ui():
    """Open the OpenShift web console in a browser for the current namespace."""

    kube = get_kube_context_info()
    if not is_openshift_cluster(kube):
        rich.print("[error] ❌ The current Kubernetes cluster is not of the OpenShift distribution.")

    api = CustomObjectsApi(kube.api_client)
    namespace = kube.namespace

    try:
        route = api.get_namespaced_custom_object(
            group="route.openshift.io",
            version="v1",
            namespace="openshift-console",
            plural="routes",
            name="console",
        )
        host = route["spec"]["host"]
    except Exception:
        rich.print("[error] ❌ Unable to determine OpenShift console host")
        raise typer.Exit(1)

    url = f"https://{host}/k8s/ns/{namespace}/core~v1~Pod"
    webbrowser.open(url)
    rich.print(f"[success]✅  Opened OpenShift console at {url}")


# Backwards compatibility with older command name
@app.command(hidden=True)
def console():
    """Deprecated alias for the :func:`ui` command without OpenShift detection."""

    kube = get_kube_context_info()
    api = CustomObjectsApi(kube.api_client)
    namespace = kube.namespace

    try:
        route = api.get_namespaced_custom_object(
            group="route.openshift.io",
            version="v1",
            namespace="openshift-console",
            plural="routes",
            name="console",
        )
        host = route["spec"]["host"]
    except Exception:
        rich.print("[error] ❌ Unable to determine OpenShift console host")
        raise typer.Exit(1)

    url = f"https://{host}/k8s/ns/{namespace}"
    webbrowser.open(url)
    rich.print(f"[success]✅  Opened OpenShift console at {url}")
