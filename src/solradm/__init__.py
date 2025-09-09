"""solradm package initialization."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import tomllib

try:  # pragma: no cover - executed when package metadata is available
    __version__ = metadata.version("solradm")
except metadata.PackageNotFoundError:  # pragma: no cover - fallback for local sources
    try:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        __version__ = tomllib.loads(pyproject.read_text())['project']['version']
    except Exception:  # pragma: no cover - last resort fallback
        __version__ = "0.0.0"

__all__ = ["__version__"]
