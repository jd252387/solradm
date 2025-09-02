from pathlib import Path

import rich
from rich.prompt import Prompt

from solradm.config.util import _validate_config_dir


def setup() -> Path:
    path: Path | None = None
    rich.print(
        "Let's set up your default configuration directory. It will be used as the default relative path for uploads and such. This is the [magenta bold]\"zNodes\"[/] under solr-source (it should contain the subdirectories [green bold]\"configsets\"[/] and [green bold]\"root\"[/].\nMake sure you put the primary repository path on your computer, where you commonly work on solr-source.")
    while path is None:
        directory = Prompt.ask("[question]Enter the path to the default configuration directory -> ")
        candidate = Path(directory).expanduser()
        if _validate_config_dir(candidate):
            path = candidate
        else:
            rich.print("[error] ❌ Directory must contain 'root' and 'configsets' subdirectories")
    return path
