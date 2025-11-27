import re
from typing import Any, List

from kubernetes.client import CoreV1Api, V1PodList, V1Pod, Configuration
from kubernetes.config import list_kube_config_contexts, load_kube_config
from kubernetes.stream import stream

from solradm.config.util import get_current_context
from solradm.exceptions.adm_exception import AdmException


def get_kubecontext(name: str) -> Any | None:
    contexts, _ = list_kube_config_contexts()
    return next((context for context in contexts if context["name"] == name), None)


def get_configured_kubecontext() -> Any | None:
    current = get_current_context()
    if not current.kubecontext:
        return None
    return get_kubecontext(current.kubecontext)


def get_current_kubecontext() -> Any | None:
    contexts, active = list_kube_config_contexts()

    return active


def get_current_kubecontext_namespace() -> str | None:
    cfg = Configuration.get_default_copy()
    if getattr(cfg, "namespace", None):
        return cfg.namespace

    active = get_current_kubecontext()
    namespace = active["context"].get("namespace") if active else None
    if namespace:
        return namespace
    return get_current_context().namespace


def switch_current_kubecontext(
        target_context: Any,
        namespace: str | None = None,
        client_configuration: Configuration | None = None,
) -> Any | None:
    load_kube_config(context=target_context["name"], client_configuration=client_configuration)

    resolved_namespace = namespace or target_context["context"].get("namespace")
    if resolved_namespace:
        if client_configuration is not None:
            client_configuration.namespace = resolved_namespace
            Configuration.set_default(client_configuration)
        else:
            cfg = Configuration.get_default_copy()
            cfg.namespace = resolved_namespace
            Configuration.set_default(cfg)


def find_pods(pattern: re.Pattern) -> List[V1Pod]:
    namespace = get_current_kubecontext_namespace()

    if not namespace:
        raise AdmException("The kubecontext does not map to a specific namespace!")

    v1 = CoreV1Api()
    pods: V1PodList = v1.list_namespaced_pod(namespace)

    return [pod for pod in pods.items if pattern.search(pod.metadata.name)]


def find_pods_by_node_name(node_name: str):
    without_subnet = node_name.partition(".")
    namespace = get_current_kubecontext_namespace()
    pod_name = without_subnet[0].partition(namespace)[2][1:]

    return find_pods(re.compile(pod_name))


def run_command_in_pod(pod_name: str, command: str) -> str:
    return stream(CoreV1Api().connect_get_namespaced_pod_exec,
        name=pod_name,
        namespace=get_current_kubecontext_namespace(),
        command=["/bin/sh", "-c", command],
        container="solrcloud-node",
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
