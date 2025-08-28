import aiohttp
from aiohttp import BasicAuth

from solradm.config import settings

_session: aiohttp.ClientSession | None = None

def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(auth=BasicAuth(settings.auth.user, settings.auth.password))
    return _session