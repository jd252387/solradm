from typing import List

from .utils import _filter


def node_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.api.state import get_nodes_by_role

        nodes = set()
        for role in ["coordinator", "data", "overseer"]:
            role_nodes = get_nodes_by_role(role)
            for arr in role_nodes.values():
                nodes.update(arr)
        options = sorted(nodes)
    except Exception:
        options = []
    return _filter(options, incomplete)
