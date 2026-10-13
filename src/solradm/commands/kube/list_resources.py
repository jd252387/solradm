import re
from typing import Literal

import rich
import typer
from kubernetes.client import AppsV1Api, CoreV1Api, CustomObjectsApi
from rich.table import Table

from solradm.commands.kube.common import is_openshift_cluster
from solradm.commands.kube.subapp import app
from solradm.config.util import get_current_context
from solradm.kube.utils import KubeContextInfo, get_kube_context_info

def _fetch(list_fn, namespace: str, pattern: re.Pattern[str] | None, label_selectors: list[str]) -> list:
    """List resources by label selector (unioned by name) or all of them, then regex-filter on name."""
    if label_selectors:
        by_name = {}
        for selector in label_selectors:
            for item in list_fn(namespace, label_selector=selector).items:
                by_name[item.metadata.name] = item
        items = list(by_name.values())
    else:
        items = list(list_fn(namespace).items)

    if pattern is not None:
        items = [i for i in items if pattern.search(i.metadata.name)]

    return sorted(items, key=lambda i: i.metadata.name)


# Compute resources shown per container, in column order. These are the keys
# Kubernetes puts in resources.requests/limits; any of them may be absent.
RESOURCE_KEYS = ("cpu", "memory", "ephemeral-storage")
RESOURCE_HEADERS = ("CPU (req/lim)", "Memory (req/lim)", "Ephemeral (req/lim)")


def _container_cells(containers: list) -> tuple[str, ...]:
    """Return (names, images, then req/lim per RESOURCE_KEYS), one line per container."""
    names, images = [], []
    resources: dict[str, list[str]] = {key: [] for key in RESOURCE_KEYS}
    for c in containers or []:
        requests = (c.resources.requests if c.resources else None) or {}
        limits = (c.resources.limits if c.resources else None) or {}
        names.append(c.name)
        images.append(c.image or "-")
        for key in RESOURCE_KEYS:
            resources[key].append(f"{requests.get(key, '-')}/{limits.get(key, '-')}")

    return ("\n".join(names), "\n".join(images),
            *("\n".join(resources[key]) for key in RESOURCE_KEYS))


def _owner_cell(owner_references) -> str:
    """Render ownerReferences as Kind/name, one line each, controller first.

    This is what names the operator CR behind a resource -- a Solr statefulset
    managed by the Solr operator is owned by SolrCloud/<name>. Accepts both
    client models and the plain dicts CustomObjectsApi returns.
    """
    refs = []
    for r in owner_references or []:
        if isinstance(r, dict):
            kind, name, controller = r.get("kind"), r.get("name"), r.get("controller")
        else:
            kind, name, controller = r.kind, r.name, r.controller
        refs.append((not controller, f"{kind}/{name}"))

    return "\n".join(text for _, text in sorted(refs)) or "-"


def _table_workloads(kube: KubeContextInfo, kind: str, list_fn, pattern, labels: list[str]) -> Table:
    table = Table(title=f"{kind} ({kube.namespace})", header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Owner")
    table.add_column("Pods", justify="right")
    table.add_column("Container")
    table.add_column("Image")
    for header in RESOURCE_HEADERS:
        table.add_column(header, justify="right")

    for w in _fetch(list_fn, kube.namespace, pattern, labels):
        containers, images, *resources = _container_cells(w.spec.template.spec.containers)
        ready = (w.status.ready_replicas or 0) if w.status else 0
        table.add_row(w.metadata.name, _owner_cell(w.metadata.owner_references),
                      f"{ready}/{w.spec.replicas}", containers, images, *resources)

    return table


def _table_pods(kube: KubeContextInfo, pattern, labels: list[str]) -> Table:
    table = Table(title=f"Pods ({kube.namespace})", header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Owner")
    table.add_column("Node")
    table.add_column("Status")
    table.add_column("Ready", justify="right")
    table.add_column("Restarts", justify="right")
    table.add_column("Container")
    table.add_column("Image")
    for header in RESOURCE_HEADERS:
        table.add_column(header, justify="right")

    list_fn = CoreV1Api(kube.api_client).list_namespaced_pod
    for p in _fetch(list_fn, kube.namespace, pattern, labels):
        statuses = (p.status.container_statuses if p.status else None) or []
        containers, images, *resources = _container_cells(p.spec.containers)
        table.add_row(
            p.metadata.name,
            _owner_cell(p.metadata.owner_references),
            p.spec.node_name or "-",
            (p.status.phase if p.status else None) or "-",
            f"{sum(1 for s in statuses if s.ready)}/{len(p.spec.containers or [])}",
            str(sum(s.restart_count or 0 for s in statuses)),
            containers,
            images,
            *resources,
        )

    return table


def _table_services(kube: KubeContextInfo, pattern, labels: list[str]) -> Table:
    table = Table(title=f"Services ({kube.namespace})", header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Owner")
    table.add_column("Type")
    table.add_column("Cluster IP")
    table.add_column("Ports")
    table.add_column("Selector")

    list_fn = CoreV1Api(kube.api_client).list_namespaced_service
    for s in _fetch(list_fn, kube.namespace, pattern, labels):
        ports = ", ".join(f"{p.port}:{p.target_port}/{p.protocol}" for p in s.spec.ports or [])
        selector = ", ".join(f"{k}={v}" for k, v in (s.spec.selector or {}).items())
        table.add_row(s.metadata.name, _owner_cell(s.metadata.owner_references), s.spec.type or "-",
                      s.spec.cluster_ip or "-", ports or "-", selector or "-")

    return table


def _table_pvc(kube: KubeContextInfo, pattern, labels: list[str]) -> Table:
    table = Table(title=f"PersistentVolumeClaims ({kube.namespace})", header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Owner")
    table.add_column("Status")
    table.add_column("Capacity", justify="right")
    table.add_column("Storage Class")
    table.add_column("Volume")
    table.add_column("Access Modes")

    list_fn = CoreV1Api(kube.api_client).list_namespaced_persistent_volume_claim
    for c in _fetch(list_fn, kube.namespace, pattern, labels):
        table.add_row(
            c.metadata.name,
            _owner_cell(c.metadata.owner_references),
            (c.status.phase if c.status else None) or "-",
            ((c.status.capacity if c.status else None) or {}).get("storage", "-"),
            c.spec.storage_class_name or "-",
            c.spec.volume_name or "-",
            ", ".join(c.spec.access_modes or []) or "-",
        )

    return table


def _table_routes(kube: KubeContextInfo, pattern, labels: list[str]) -> Table:
    if not is_openshift_cluster(kube):
        rich.print("[error] ❌ The current Kubernetes cluster is not of the OpenShift distribution; it has no routes.")
        raise typer.Exit(1)

    api = CustomObjectsApi(kube.api_client)

    def _list(selector: str | None = None) -> list[dict]:
        extra = {"label_selector": selector} if selector else {}
        response = api.list_namespaced_custom_object(
            group="route.openshift.io",
            version="v1",
            namespace=kube.namespace,
            plural="routes",
            **extra,
        )
        return response.get("items", [])

    if labels:
        routes = list({r["metadata"]["name"]: r for selector in labels for r in _list(selector)}.values())
    else:
        routes = _list()

    if pattern is not None:
        routes = [r for r in routes if pattern.search(r["metadata"]["name"])]

    table = Table(title=f"Routes ({kube.namespace})", header_style="bold magenta")
    table.add_column("Name", style="cyan")
    table.add_column("Owner")
    table.add_column("Host")
    table.add_column("Service")
    table.add_column("Target Port")
    table.add_column("TLS")

    for r in sorted(routes, key=lambda r: r["metadata"]["name"]):
        spec = r.get("spec") or {}
        backends = [(spec.get("to") or {}).get("name")] + [
            b.get("name") for b in (spec.get("alternateBackends") or [])
        ]
        table.add_row(
            r["metadata"]["name"],
            _owner_cell(r["metadata"].get("ownerReferences")),
            spec.get("host") or "-",
            ", ".join(b for b in backends if b) or "-",
            str((spec.get("port") or {}).get("targetPort", "-")),
            (spec.get("tls") or {}).get("termination") or "-",
        )

    return table


_LIST_BUILDERS = {
    "deployments": lambda kube, pattern, labels: _table_workloads(
        kube, "Deployments", AppsV1Api(kube.api_client).list_namespaced_deployment, pattern, labels
    ),
    "statefulsets": lambda kube, pattern, labels: _table_workloads(
        kube, "StatefulSets", AppsV1Api(kube.api_client).list_namespaced_stateful_set, pattern, labels
    ),
    "pods": _table_pods,
    "services": _table_services,
    "pvc": _table_pvc,
    "routes": _table_routes,
}


@app.command("list", help="List Kubernetes resources in the current namespace")
def list_resources(
        resource: Literal["deployments", "statefulsets", "pods", "services", "pvc", "routes"] = typer.Argument(
            ..., help="Resource type to list"),
        pattern: str | None = typer.Option(None, "--pattern", "-p", help="Regex to filter by resource name"),
        label: list[str] | None = typer.Option(None, "--label", "-l",
                                               help="Label selector (can be specified multiple times)"),
) -> None:
    """Display a table of the requested resource type in the current context's namespace."""
    try:
        compiled = re.compile(pattern) if pattern else None
    except re.error as e:
        raise typer.BadParameter(f"Invalid regex pattern '{pattern}': {e}")

    kube = get_kube_context_info(get_current_context())
    table = _LIST_BUILDERS[resource](kube, compiled, label or [])

    if not table.row_count:
        rich.print(f"[warning]ℹ️  No {resource} found in namespace '{kube.namespace}'.")
        return

    rich.print(table)
