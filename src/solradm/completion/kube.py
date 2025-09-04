import re
from typing import List

from .utils import _filter


def pod_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.commands.kube import load_configured_kubecontext
        from solradm.kube.utils import find_pods

        load_configured_kubecontext()
        pods = find_pods(re.compile(""))
        names = [p.metadata.name for p in pods]
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)


def container_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.commands.kube import load_configured_kubecontext
        from solradm.kube.utils import find_pods

        load_configured_kubecontext()
        pods = find_pods(re.compile(""))
        containers = {c.name for p in pods for c in p.spec.containers}
        names = sorted(containers)
    except Exception:
        names = []
    return _filter(names, incomplete)


def workload_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.commands.kube import load_configured_kubecontext, _get_workloads

        load_configured_kubecontext()
        deployments, statefulsets = _get_workloads(re.compile(""))
        names = [d.metadata.name for d in deployments] + [s.metadata.name for s in statefulsets]
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)
