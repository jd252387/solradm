from kazoo.client import KazooClient
import logging
from solradm.config.util import get_current_context

logging.getLogger("kazoo").setLevel(logging.CRITICAL)

_client: KazooClient | None = None


def get_client() -> KazooClient:
    global _client
    if _client is None:
        _client = KazooClient(hosts=get_current_context().zk, timeout=5)
        _client.start()
    return _client