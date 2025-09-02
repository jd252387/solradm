from .utils import _filter


def replica_types(ctx, param, incomplete: str):
    return _filter(["leader", "follower"], incomplete)


def replica_states(ctx, param, incomplete: str):
    options = ["active", "down", "recovering", "recovery_failed"]
    return _filter(options, incomplete)
