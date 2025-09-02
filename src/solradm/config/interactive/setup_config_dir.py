from pathlib import Path
import rich
from rich.prompt import Prompt

from solradm.config.util import validate_config_dir


def setup() -> Path:
    path: Path | None = None
    while path is None:
        directory = Prompt.ask("[question]Enter the path to the default configuration directory -> ")
        candidate = Path(directory).expanduser()
        if validate_config_dir(candidate):
            path = candidate
        else:
            rich.print("[error] ❌ Directory must contain 'root' and 'configsets' subdirectories")
    return path
