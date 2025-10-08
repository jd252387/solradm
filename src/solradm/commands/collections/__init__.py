from __future__ import annotations

from .app import app

# Import command modules to register CLI subcommands when the package is loaded.
# The imported modules attach commands to the shared Typer application via decorators.
from . import data_io as _data_io  # noqa: F401
from . import lifecycle as _lifecycle  # noqa: F401
from . import maintenance as _maintenance  # noqa: F401
from . import query as _query  # noqa: F401
from . import reindex as _reindex  # noqa: F401
from .lifecycle import _select_nodes
from .maintenance import reload

__all__ = ["app", "_select_nodes", "reload"]
