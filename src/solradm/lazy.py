"""Lightweight utilities for lazily importing modules without warnings."""

from __future__ import annotations

from importlib import import_module
from typing import Any


class _LazyModule:
    """Proxy object that loads the target module on first attribute access."""

    def __init__(self, name: str):
        self._name = name
        self._module: Any | None = None

    def _load(self) -> Any:
        if self._module is None:
            self._module = import_module(self._name)
        return self._module

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - simple delegation
        return getattr(self._load(), item)


def lazy_module(name: str) -> _LazyModule:
    """Return a proxy that lazily imports ``name`` when accessed."""

    return _LazyModule(name)

