from typing import List

from . import autocompletion_error
from .utils import _filter_starts_with


def node_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.api.state import get_nodes_by_role

        nodes = set()
        for role in ["coordinator", "data", "overseer"]:
            role_nodes = get_nodes_by_role(role)
            for arr in role_nodes.values():
                nodes.update(arr)
        options = sorted(nodes)
    except Exception as e:
        return autocompletion_error(incomplete, e)
    return _filter_starts_with(options, incomplete)
