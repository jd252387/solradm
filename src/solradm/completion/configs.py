from typing import List

from .utils import _filter


def config_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.zk import get_client

        zk = get_client()
        if zk.exists("/configs"):
            options = sorted(zk.get_children("/configs"))
        else:
            options = []
    except Exception:
        options = []
    return _filter(options, incomplete)
