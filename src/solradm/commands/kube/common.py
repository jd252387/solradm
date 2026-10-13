"""Helpers shared by more than one kube command.

Named `common` rather than `utils` because `solradm.kube.utils` already exists
and holds the lower-level client plumbing this builds on.
"""
import re
from pathlib import Path

import rich
from kubernetes import client
from kubernetes.client import ApiException, AppsV1Api
from platformdirs import user_config_dir
from rich.table import Table

from solradm.kube.utils import KubeContextInfo

STATE_DIR = Path(user_config_dir("solradm", "eclipse")) / "kube-scale-state"


def _ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


def is_openshift_cluster(kube: KubeContextInfo) -> bool:
    try:
        groups = client.ApisApi(api_client=kube.api_client).get_api_versions().groups

        for group in groups:
            if group.name == "route.openshift.io":
                return True
    except ApiException as e:
        rich.print(f"[error] ❌ ApiExtensions request failed: {e}")
        return False

    return False


def _get_workloads_by_pattern(kube: KubeContextInfo, pattern: re.Pattern[str], namespace: str | None = None) -> tuple[list, list]:
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


def _get_workloads_by_labels(kube: KubeContextInfo, label_selectors: list[str], namespace: str | None = None) -> tuple[list, list]:
    namespace = namespace or kube.namespace
    api = AppsV1Api(kube.api_client)

    deployment_by_name = {}
    statefulset_by_name = {}
    for selector in label_selectors:
        for deployment in api.list_namespaced_deployment(namespace, label_selector=selector).items:
            deployment_by_name[deployment.metadata.name] = deployment
        for statefulset in api.list_namespaced_stateful_set(namespace, label_selector=selector).items:
            statefulset_by_name[statefulset.metadata.name] = statefulset

    return list(deployment_by_name.values()), list(statefulset_by_name.values())


def _print_workloads(deployments: list, statefulsets: list) -> None:
    table = Table(title="Workloads", header_style="bold magenta")
    table.add_column("Type")
    table.add_column("Name")
    table.add_column("Replicas", justify="right")
    for d in deployments:
        table.add_row("Deployment", d.metadata.name, str(d.spec.replicas))
    for s in statefulsets:
        table.add_row("StatefulSet", s.metadata.name, str(s.spec.replicas))
    rich.print(table)
