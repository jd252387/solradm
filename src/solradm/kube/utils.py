from typing import Any

from kubernetes.config import list_kube_config_contexts


def get_kubecontext(name: str) -> Any | None:
    contexts, _ = list_kube_config_contexts()
    return next((context for context in contexts if context["name"] == name), None)