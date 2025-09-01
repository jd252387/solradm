from pathlib import Path

import rich
from rich.prompt import Prompt

from solradm.config.util import is_valid_config_dir


def setup() -> Path:
    directory = ""
    while True:
        directory = Prompt.ask("[question]Enter the path to your default configuration directory -> ")
        path = Path(directory).expanduser()
        if is_valid_config_dir(path):
            return path
        rich.print("[error]❌  Directory must contain 'root' and 'configsets' subdirectories")

