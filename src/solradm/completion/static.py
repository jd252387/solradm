from typing import List

from .utils import _filter_starts_with


def replica_types(ctx, args: List[str], incomplete: str) -> List[str]:
    return _filter_starts_with(["leader", "follower"], incomplete)


def replica_states(ctx, args: List[str], incomplete: str) -> List[str]:
    options = ["active", "down", "recovering", "recovery_failed"]
    return _filter_starts_with(options, incomplete)
