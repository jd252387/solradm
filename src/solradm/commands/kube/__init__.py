import asyncio
import json
import re
import webbrowser
from itertools import cycle
from pathlib import Path
from typing import Any

import rich
import typer
import urllib3
from async_typer import AsyncTyper
from kubernetes import client
from kubernetes.client import ApiClient, Configuration, ApiException
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
from solradm.config.util import get_current_context
from solradm.exceptions.adm_exception import AdmException
from solradm.kube.utils import (
    get_configured_kubecontext,
    get_kubecontext,
    find_pods,
    find_pods_by_node_name,
    get_current_kubecontext_namespace,
    run_command_in_pod,
    switch_current_kubecontext,
)
from solradm.utils import open_directory

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = AsyncTyper()
add_verbosity_option(app)

STATE_DIR = Path(user_config_dir("solradm", "eclipse")) / "kube-states"


def get_state_file_path(kubecontext: str) -> Path:
    """Get the state file path for a given kubecontext."""
    # Sanitize the kubecontext name for use in filename
    safe_name = re.sub(r'[^\w\-.]', '_', kubecontext)
    return STATE_DIR / f"state-{safe_name}.json"


def load_and_validate_kubecontext(kubecontext: str) -> tuple[Any, str]:
    """
    Load and validate a kubecontext by name.
    Returns the context object and its namespace.
    Raises AdmException if the context is invalid or has no namespace.
    """
    context = get_kubecontext(kubecontext)

    if context is None:
        raise AdmException(
            f"Kubecontext '{kubecontext}' could not be found in your kubeconfig."
        )

    namespace = context["context"].get("namespace")
    if not namespace:
        raise AdmException(
            f"Kubecontext '{kubecontext}' is missing a namespace configuration."
        )

    # Load the kubeconfig for this context
    switch_current_kubecontext(context, namespace=namespace)

    return context, namespace


def load_configured_kubecontext(client_configuration: Configuration = None) -> bool:
    current_context = get_current_context()
    configured = get_configured_kubecontext()

    if not current_context.kubecontext:
        raise AdmException("No kubecontext is configured for the current context!")

    if configured is None:
        raise AdmException(
            f"Kubecontext {current_context.kubecontext} could not be found in your kubeconfig."
        )

    if not current_context.namespace:
        raise AdmException(
            "The configured kubecontext is missing a namespace. Edit the context to add one."
        )

    switch_current_kubecontext(
        configured,
        namespace=current_context.namespace,
        client_configuration=client_configuration,
    )

    return True

def is_openshift_cluster() -> bool:
    try:
        groups = client.ApisApi().get_api_versions().groups

        for group in groups:
            if group.name == "route.openshift.io":
                return True
    except ApiException as e:
        rich.print(f"[error] ❌ ApiExtensions request failed: {e}")
        return False

    return False

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


@app.async_command(help="Stream logs for matching pods")
async def logs(
        pattern: str = typer.Argument(..., help="Regex of pod or node name", autocompletion=pod_names),
        node: bool = typer.Option(False, "--node", help="Treat pattern as node name", autocompletion=node_names),
        container: str | None = typer.Option(None, "--container", "-c", help="Container name",
                                             autocompletion=container_names),
):
    """Stream Kubernetes logs from pods matching PATTERN."""
    load_configured_kubecontext()
    pods = find_pods_by_node_name(pattern) if node else find_pods(re.compile(pattern))

    if not pods:
        raise typer.BadParameter("No pods matched the given pattern")

    namespace = get_current_kubecontext_namespace()

    console = Console()
    color_cycle = cycle(["red", "green", "yellow", "blue", "magenta", "cyan"])
    pod_colors = {p.metadata.name: next(color_cycle) for p in pods}

    def _stream_logs(pod_name: str):
        resp = CoreV1Api().read_namespaced_pod_log(
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

    load_configured_kubecontext()
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
        output = await asyncio.to_thread(run_command_in_pod, pod_name, "df -h /var/solr/data")
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
        kubecontext: str = typer.Argument(..., help="Kubecontext name to suspend workloads in"),
        name_regex: str = typer.Argument(..., help="Regex for deployment/statefulset names",
                                         autocompletion=workload_names),
):
    """Scale matching deployments and statefulsets to zero replicas."""

    # Load and validate the kubecontext
    context, namespace = load_and_validate_kubecontext(kubecontext)

    # Get the state file path for this context
    state_file = get_state_file_path(kubecontext)

    # Check if state file already exists
    if state_file.exists():
        rich.print(f"[error] ❌ State file for kubecontext '{kubecontext}' already exists at {state_file}")
        rich.print("[error] ❌ Please resume the existing state first before suspending again")
        raise typer.Exit(1)

    pattern = re.compile(name_regex)
    deployments, statefulsets = _get_workloads(pattern)
    if not deployments and not statefulsets:
        rich.print("[error] ❌ No deployments or statefulsets match the given pattern")
        raise typer.Exit(1)

    _print_workloads(deployments, statefulsets)
    if not Confirm.ask("Proceed with scaling these workloads to zero?"):
        raise typer.Exit(0)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "kubecontext": kubecontext,
        "namespace": namespace,
        "deployments": {d.metadata.name: d.spec.replicas for d in deployments},
        "statefulsets": {s.metadata.name: s.spec.replicas for s in statefulsets},
    }
    with open(state_file, "w") as f:
        json.dump(data, f, indent=2)

    api = AppsV1Api()
    for d in deployments:
        api.patch_namespaced_deployment_scale(d.metadata.name, namespace, {"spec": {"replicas": 0}})
    for s in statefulsets:
        api.patch_namespaced_stateful_set_scale(s.metadata.name, namespace, {"spec": {"replicas": 0}})

    rich.print(f"[success]✅  Scaled workloads and saved state to {state_file}")


@app.command(help="Restore replicas from a saved state file")
def resume(
        kubecontext: str = typer.Argument(..., help="Kubecontext name to resume workloads in"),
):
    """Scale previously suspended workloads back to their original replicas."""

    # Get the state file path for this context
    state_file = get_state_file_path(kubecontext)

    if not state_file.exists():
        rich.print(f"[error] ❌ State file for kubecontext '{kubecontext}' does not exist at {state_file}")
        rich.print("[error] ❌ There is no suspended state to resume for this context")
        raise typer.Exit(1)

    with open(state_file) as f:
        data = json.load(f)

    # Verify the state file matches the requested kubecontext
    saved_context = data.get("kubecontext")
    if saved_context != kubecontext:
        rich.print(f"[error] ❌ State file mismatch: file contains context '{saved_context}', but '{kubecontext}' was requested")
        raise typer.Exit(1)

    # Load and validate the kubecontext
    context, namespace = load_and_validate_kubecontext(kubecontext)

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
    for name, replicas in deployments.items():
        api.patch_namespaced_deployment_scale(name, namespace, {"spec": {"replicas": replicas}})
    for name, replicas in statefulsets.items():
        api.patch_namespaced_stateful_set_scale(name, namespace, {"spec": {"replicas": replicas}})

    rich.print(f"[success]✅  Restored workloads from {state_file}")

    # Delete the state file after successful resume
    state_file.unlink()
    rich.print(f"[success]✅  Deleted state file {state_file}")


@app.command(help="Open the directory containing kube state files")
def dir():
    """Open the directory containing kube suspend/resume state files."""
    open_directory(STATE_DIR)


@app.command(help="Open OpenShift console for the current namespace")
def ui():
    """Open the OpenShift web console in a browser for the current namespace."""

    load_configured_kubecontext()
    if not is_openshift_cluster():
        rich.print("[error] ❌ The current Kubernetes cluster is not of the OpenShift distribution.")

    api = CustomObjectsApi(ApiClient())
    namespace = get_current_kubecontext_namespace()
    if not namespace:
        rich.print("[error] ❌ The kubecontext does not map to a specific namespace!")
        raise typer.Exit(1)

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

    load_configured_kubecontext()
    api = CustomObjectsApi(ApiClient())
    namespace = get_current_kubecontext_namespace()
    if not namespace:
        rich.print("[error] ❌ The kubecontext does not map to a specific namespace!")
        raise typer.Exit(1)

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
