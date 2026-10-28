import rich
import typer

from solradm.commands.kube.common import _ensure_state_dir
from solradm.commands.kube.subapp import app

@app.command(help="Open the directory that stores saved kube workload states")
def dir() -> None:
    """Open the directory containing saved kube workload state files."""

    directory = _ensure_state_dir()
    typer.launch(str(directory))
    rich.print(f"[success]✅  Opened kube state directory at {directory}")
