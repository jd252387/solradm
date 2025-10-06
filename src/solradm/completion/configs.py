from typing import List

from . import autocompletion_error
from .utils import _filter_starts_with


def config_names(ctx, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.zk import get_client

        zk = get_client()
        if zk.exists("/configs"):
            options = sorted(zk.get_children("/configs"))
        else:
            options = []
    except Exception as e:
        return autocompletion_error(incomplete, e)
    return _filter_starts_with(options, incomplete)
