from .static import replica_states, replica_types


def collection_names(ctx, param, incomplete: str):
    from .collections import collection_names as _collection_names

    return _collection_names(ctx, param, incomplete)


def source_collection_names(ctx, param, incomplete: str):
    from .collections import source_collection_names as _source_collection_names

    return _source_collection_names(ctx, param, incomplete)


def shard_numbers(ctx, param, incomplete: str):
    from .collections import shard_numbers as _shard_numbers

    return _shard_numbers(ctx, param, incomplete)


def replica_positions(ctx, param, incomplete: str):
    from .collections import replica_positions as _replica_positions

    return _replica_positions(ctx, param, incomplete)


def node_names(ctx, param, incomplete: str):
    from .nodes import node_names as _node_names

    return _node_names(ctx, param, incomplete)


def config_names(ctx, param, incomplete: str):
    from .configs import config_names as _config_names

    return _config_names(ctx, param, incomplete)


def context_names(ctx, param, incomplete: str):
    from .contexts import context_names as _context_names

    return _context_names(ctx, param, incomplete)


def kube_contexts(ctx, param, incomplete: str):
    from .contexts import kube_contexts as _kube_contexts

    return _kube_contexts(ctx, param, incomplete)


def context_repo_paths(ctx, param, incomplete: str):
    from .contexts import context_repo_paths as _context_repo_paths

    return _context_repo_paths(ctx, param, incomplete)

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
    "context_repo_paths",
]
