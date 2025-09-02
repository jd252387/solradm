from typing import List

from .utils import _filter


def context_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.config import settings

        names = [c.name for c in settings.contexts.available]
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)


def kube_contexts(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from kubernetes.config import list_kube_config_contexts

        contexts, _ = list_kube_config_contexts()
        names = [c["name"] for c in contexts]
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)
