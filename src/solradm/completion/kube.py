import re
from typing import List

from .utils import _filter_starts_with


def pod_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.kube.utils import find_pods, get_kube_context_info

        kube = get_kube_context_info()
        pods = find_pods(kube, re.compile(""))
        names = [p.metadata.name for p in pods]
    except Exception:
        names = []
    return _filter_starts_with(sorted(names), incomplete)


def container_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.kube.utils import find_pods, get_kube_context_info

        kube = get_kube_context_info()
        pods = find_pods(kube, re.compile(""))
        containers = {c.name for p in pods for c in p.spec.containers}
        names = sorted(containers)
    except Exception:
        names = []
    return _filter_starts_with(names, incomplete)


def workload_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.commands.kube import _get_workloads
        from solradm.kube.utils import get_kube_context_info

        kube = get_kube_context_info()
        deployments, statefulsets = _get_workloads(kube, re.compile(""))
        names = [d.metadata.name for d in deployments] + [s.metadata.name for s in statefulsets]
    except Exception:
        names = []
    return _filter_starts_with(sorted(names), incomplete)
