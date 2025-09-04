from typing import List

from .utils import _filter


def znode_paths(ctx, args: List[str], incomplete: str) -> List[str]:
    path = incomplete if incomplete.startswith('/') else '/' + incomplete
    try:
        from solradm.zk import get_client
        zk = get_client()
        parent = path.rsplit('/', 1)[0] if '/' in path else '/'
        children = zk.get_children(parent)
        opts = [f"{parent.rstrip('/')}/{c}" for c in children] + ["/"]
    except Exception:
        opts = []
    return _filter(sorted(opts), path)
