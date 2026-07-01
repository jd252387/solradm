from . import artifact, download, setup, upload  # noqa: F401 — register commands on the shared app
from .subapp import app

__all__ = ["app"]
