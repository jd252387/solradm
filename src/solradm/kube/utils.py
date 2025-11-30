import re
from dataclasses import dataclass
from typing import Any, List

from kubernetes import config
from kubernetes.client import ApiClient, CoreV1Api, V1PodList, V1Pod
from kubernetes.config import list_kube_config_contexts
from kubernetes.stream import stream

from solradm.config.util import get_current_context
from solradm.exceptions.adm_exception import AdmException


@dataclass
class KubeContextInfo:
    api_client: ApiClient
    name: str
    namespace: str


def get_kubecontext(name: str) -> Any | None:
    contexts, _ = list_kube_config_contexts()
    return next((context for context in contexts if context["name"] == name), None)


def get_kube_context_info(
    kubecontext: str | None = None, namespace: str | None = None
) -> KubeContextInfo:
    current_context = get_current_context()
    target_context_name = kubecontext or current_context.kubecontext

    if not target_context_name:
        raise AdmException(
            "The current context does not define a kubecontext. Edit the context and add one to it."
        )

    target_context = get_kubecontext(target_context_name)
    if target_context is None:
        raise AdmException(
            f"Kubecontext {target_context_name} could not be found in your kubeconfig."
        )

    resolved_namespace = (
        namespace or current_context.namespace or target_context["context"].get("namespace")
    )

    if not resolved_namespace:
        raise AdmException("The kubecontext does not map to a specific namespace!")

    api_client = config.new_client_from_config(context=target_context_name)

    return KubeContextInfo(api_client=api_client, name=target_context_name, namespace=resolved_namespace)


def find_pods(kube: KubeContextInfo, pattern: re.Pattern) -> List[V1Pod]:
    v1 = CoreV1Api(kube.api_client)
    pods: V1PodList = v1.list_namespaced_pod(kube.namespace)

    return [pod for pod in pods.items if pattern.search(pod.metadata.name)]


def find_pods_by_node_name(kube: KubeContextInfo, node_name: str):
    without_subnet = node_name.partition(".")
    pod_name = without_subnet[0].partition(kube.namespace)[2][1:]

    return find_pods(kube, re.compile(pod_name))


def run_command_in_pod(kube: KubeContextInfo, pod_name: str, command: str) -> str:
    return stream(
        CoreV1Api(kube.api_client).connect_get_namespaced_pod_exec,
        name=pod_name,
        namespace=kube.namespace,
        command=["/bin/sh", "-c", command],
        container="solrcloud-node",
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )
