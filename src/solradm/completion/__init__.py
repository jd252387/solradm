from typing import List
from rich.console import Console

err_console = Console(stderr=True)

def autocompletion_error(incomplete: str, e: Exception) -> List[str]:
    err_console.print(f"{e}")
    return [incomplete]