from typing import List

from .static import replica_types, replica_states


def collection_names(ctx, args: List[str], incomplete: str) -> List[str]:
    from .collections import collection_names as _collection_names

    return _collection_names(ctx, args, incomplete)


def source_collection_names(ctx, args: List[str], incomplete: str) -> List[str]:
    from .collections import source_collection_names as _source_collection_names

    return _source_collection_names(ctx, args, incomplete)


def shard_numbers(ctx, args: List[str], incomplete: str) -> List[str]:
    from .collections import shard_numbers as _shard_numbers

    return _shard_numbers(ctx, args, incomplete)


def replica_positions(ctx, args: List[str], incomplete: str) -> List[str]:
    from .collections import replica_positions as _replica_positions

    return _replica_positions(ctx, args, incomplete)


def node_names(ctx, args: List[str], incomplete: str) -> List[str]:
    from .nodes import node_names as _node_names

    return _node_names(ctx, args, incomplete)


def config_names(ctx, args: List[str], incomplete: str) -> List[str]:
    from .configs import config_names as _config_names

    return _config_names(ctx, args, incomplete)


def context_names(ctx, args: List[str], incomplete: str) -> List[str]:
    from .contexts import context_names as _context_names

    return _context_names(ctx, args, incomplete)


def kube_contexts(ctx, args: List[str], incomplete: str) -> List[str]:
    from .contexts import kube_contexts as _kube_contexts

    return _kube_contexts(ctx, args, incomplete)

__all__ = [
    "collection_names",
    "source_collection_names",
    "shard_numbers",
    "replica_types",
    "replica_states",
    "replica_positions",
    "node_names",
    "config_names",
    "context_names",
    "kube_contexts",
]
