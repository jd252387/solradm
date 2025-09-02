import re
from .utils import _filter


def collection_names(ctx, param, incomplete: str):
    try:
        from solradm.api.state import get_collection_names

        names = get_collection_names()
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)


def source_collection_names(ctx, param, incomplete: str):
    try:
        context = ctx.params.get("source_context")
        if context:
            from kazoo.client import KazooClient
            from solradm.config import settings

            ctx_obj = next(
                (c for c in settings.contexts.available if c.name == context), None
            )
            if not ctx_obj:
                names = []
            else:
                zk = KazooClient(hosts=ctx_obj.zk, timeout=5)
                zk.start()
                try:
                    names = zk.get_children("/collections")
                finally:
                    zk.stop()
                    zk.close()
        else:
            from solradm.api.state import get_collection_names

            names = get_collection_names()
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)


def shard_numbers(ctx, param, incomplete: str):
    try:
        from solradm.api.state import get_collections

        nums = set()
        for coll in get_collections():
            for shard in coll.shards:
                m = re.search(r"\d+", shard.name)
                if m:
                    nums.add(m.group(0))
        options = sorted(nums)
    except Exception:
        options = []
    return _filter(options, incomplete)


def replica_positions(ctx, param, incomplete: str):
    try:
        from solradm.api.state import get_collections

        max_pos = 0
        for coll in get_collections():
            for shard in coll.shards:
                max_pos = max(max_pos, len(shard.replicas))
        options = [str(i) for i in range(1, max_pos + 1)]
    except Exception:
        options = []
    return _filter(options, incomplete)
