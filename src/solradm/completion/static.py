from typing import List

from .utils import _filter


def replica_types(ctx, args: List[str], incomplete: str) -> List[str]:
    return _filter(["leader", "follower"], incomplete)


def replica_states(ctx, args: List[str], incomplete: str) -> List[str]:
    options = ["active", "down", "recovering", "recovery_failed"]
    return _filter(options, incomplete)
