from typing import List

from .utils import _filter_starts_with


def replica_types(ctx, args: List[str], incomplete: str) -> List[str]:
    return _filter_starts_with(["leader", "follower"], incomplete)


REPLICA_STATES = ["active", "down", "recovering", "recovery_failed"]


def replica_states(ctx, args: List[str], incomplete: str) -> List[str]:
    return _filter_starts_with(REPLICA_STATES, incomplete)
