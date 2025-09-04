import re
from typing import Any, List

from kubernetes.client import CoreV1Api, V1PodList, V1Pod
from kubernetes.config import list_kube_config_contexts, load_kube_config

from solradm.config.util import get_current_context
from solradm.exceptions.adm_exception import AdmException


def get_kubecontext(name: str) -> Any | None:
    contexts, _ = list_kube_config_contexts()
    return next((context for context in contexts if context["name"] == name), None)


def get_configured_kubecontext() -> Any | None:
    return get_kubecontext(get_current_context().kubecontext)


def get_current_kubecontext() -> Any | None:
    contexts, active = list_kube_config_contexts()

    return active


def get_current_kubecontext_namespace() -> str | None:
    return get_current_kubecontext()["context"].get("namespace")


def switch_current_kubecontext(target_context: Any) -> Any | None:
    load_kube_config(context=target_context["name"])


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
    pod_name = without_subnet[0].partition(namespace)[2]

    return find_pods(re.compile(pod_name))


def run_command_in_pod(pod_name: str, command: str) -> str:
    return CoreV1Api().connect_get_namespaced_pod_exec(
        name=pod_name,
        namespace=get_current_kubecontext_namespace(),
        command=["/bin/sh", "-c", command],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
