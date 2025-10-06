from typing import List


def _filter_starts_with(options: List[str], incomplete: str) -> List[str]:
    filtered = [o for o in options if o.startswith(incomplete)]

    if len(filtered) == 0:
        return [f"{incomplete} No matches!"]

    return filtered
