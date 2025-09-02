"""HTTP session management for solradm."""

from __future__ import annotations

from typing import TYPE_CHECKING

from solradm.config import settings

if TYPE_CHECKING:  # pragma: no cover - only for type checking
    import aiohttp

_session: "aiohttp.ClientSession | None" = None


def get_session() -> "aiohttp.ClientSession":
    """Return a globally cached aiohttp ClientSession.

    The heavy aiohttp import is deferred until this function is called to
    keep CLI start-up and autocompletion fast.
    """
    global _session
    if _session is None or _session.closed:
        import aiohttp
        from aiohttp import BasicAuth

        _session = aiohttp.ClientSession(
            auth=BasicAuth(settings.auth.user, settings.auth.password)
        )
    return _session


def get_initialized_session() -> "aiohttp.ClientSession | None":
    """Return the session if it has been created."""
    return _session
