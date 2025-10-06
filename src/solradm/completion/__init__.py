from typing import List

def autocompletion_error(incomplete: str, e: Exception) -> List[str]:
    return [f"{incomplete} Error while fetching autocompletion!", f"{incomplete} Error: {e}"]