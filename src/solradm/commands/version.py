import importlib.metadata
from pathlib import Path
import tomllib
import typer


def version() -> None:
    """Display the current solradm version."""
    try:
        ver = importlib.metadata.version("solradm")
    except importlib.metadata.PackageNotFoundError:
        try:
            pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
            with pyproject.open("rb") as fh:
                ver = tomllib.load(fh)["project"]["version"]
        except Exception:
            ver = "unknown"
    typer.echo(ver)
