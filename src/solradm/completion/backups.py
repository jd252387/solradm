from pathlib import PurePosixPath, Path
from typing import List

from pyasn1_modules.rfc4010 import id_npki_app_cmsSeed_wrap

from . import autocompletion_error
from .utils import _filter_starts_with


def backup_paths(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.commands.kube import load_configured_kubecontext
        from solradm.kube.utils import run_command_in_pod, find_pods_by_node_name
        from solradm.zk.utils import get_overseer_leader

        load_configured_kubecontext()
        pod = find_pods_by_node_name(get_overseer_leader())[0]
        path = PurePosixPath(incomplete or "/")
        base = str(path if path.is_absolute() else Path("/") / path)
        parent = base if base.endswith("/") else str(PurePosixPath(base).parent) + "/"
        output = run_command_in_pod(pod.metadata.name, f"find {parent} -maxdepth 1 -type d")
        dirs = [d.strip() + "/" for d in output.splitlines() if d.strip()]
    except Exception as e:
        return autocompletion_error(incomplete, e)
    return _filter_starts_with(sorted(dirs), incomplete)
