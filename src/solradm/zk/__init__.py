import logging

from kazoo.client import KazooClient
from kazoo.protocol.states import KazooState

from solradm.config.util import get_current_context

logging.getLogger("kazoo").setLevel(logging.CRITICAL)

_client: KazooClient | None = None


def get_client() -> KazooClient:
    """Return a connected Kazoo client.

    Historically this module cached the first ``KazooClient`` instance it
    created.  If the initial ``start()`` call failed (for example because the
    ZooKeeper host was unreachable) the partially initialised client was kept
    in the cache.  Subsequent calls would reuse this broken instance and every
    command would fail until an autocompletion triggered a successful
    connection and replaced the cached client.

    To avoid that scenario we ensure that the client is always connected when
    returned.  If ``start()`` fails we reset the cache so the next call will
    create a fresh client.
    """

    global _client

    if _client is None:
        _client = KazooClient(hosts=get_current_context().zk, timeout=5)

    if _client.state != KazooState.CONNECTED:
        try:
            _client.start()
        except Exception:
            # Reset the cached client so a fresh instance will be created on
            # the next call.  This prevents broken clients from poisoning
            # subsequent commands.
            _client.stop()
            _client.close()
            _client = None
            raise

    return _client
