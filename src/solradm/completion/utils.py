from typing import List


def _filter(options: List[str], incomplete: str) -> List[str]:
    return [o for o in options if o.startswith(incomplete)]
