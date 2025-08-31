from urllib.parse import urljoin
import aiohttp
from aiohttp import BasicAuth
import rich
from solradm.config import settings
from solradm.exceptions.solr_exception import SolrException

_session: aiohttp.ClientSession | None = None

def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(auth=BasicAuth(settings.auth.user, settings.auth.password))
    return _session

def get_initialized_sesssion() -> aiohttp.ClientSession:
    return _session