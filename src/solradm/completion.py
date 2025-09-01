import re
from typing import List

import typer

from solradm.api.state import (
    get_collection_names,
    get_collections,
    get_nodes_by_role,
)
from solradm.zk import get_client


def _filter(options: List[str], incomplete: str) -> List[str]:
    return [o for o in options if o.startswith(incomplete)]


def collection_names(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    try:
        names = get_collection_names()
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)


def shard_numbers(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    try:
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


def replica_types(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    return _filter(["leader", "follower"], incomplete)


def replica_states(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    return _filter(["active", "down", "recovering", "recovery_failed"], incomplete)


def replica_positions(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    try:
        max_pos = 0
        for coll in get_collections():
            for shard in coll.shards:
                max_pos = max(max_pos, len(shard.replicas))
        options = [str(i) for i in range(1, max_pos + 1)]
    except Exception:
        options = []
    return _filter(options, incomplete)


def node_names(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    try:
        nodes = set()
        for role in ["coordinator", "data", "overseer"]:
            role_nodes = get_nodes_by_role(role)
            for arr in role_nodes.values():
                nodes.update(arr)
        options = sorted(nodes)
    except Exception:
        options = []
    return _filter(options, incomplete)


def config_names(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    try:
        zk = get_client()
        if zk.exists("/configs"):
            options = sorted(zk.get_children("/configs"))
        else:
            options = []
    except Exception:
        options = []
    return _filter(options, incomplete)


def context_names(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    try:
        from solradm.config import settings

        names = [c.name for c in settings.contexts.available]
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)


def kube_contexts(ctx: typer.Context, args: List[str], incomplete: str) -> List[str]:
    try:
        from kubernetes.config import list_kube_config_contexts

        contexts, _ = list_kube_config_contexts()
        names = [c["name"] for c in contexts]
    except Exception:
        names = []
    return _filter(sorted(names), incomplete)
