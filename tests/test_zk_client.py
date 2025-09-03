import importlib
from pathlib import Path

import pytest


def test_get_client_recovers_after_failure(monkeypatch, tmp_path):
    """Ensure the ZooKeeper client cache recovers from a failed start."""

    # Prepare minimal configuration to avoid interactive setup during import
    cfg_dir = tmp_path / "solradm"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "settings.yaml").write_text(
        "contexts:\n  available:\n    - name: test\n      zk: dummy\n  current: {name: test}\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    import solradm.config as cfg
    importlib.reload(cfg)
    import solradm.zk as zk
    importlib.reload(zk)
    from kazoo.protocol.states import KazooState

    calls = {"start": 0}

    class DummyKazooClient:
        def __init__(self, hosts, timeout):
            self.state = KazooState.LOST
        def start(self):
            calls["start"] += 1
            if calls["start"] == 1:
                raise Exception("boom")
            self.state = KazooState.CONNECTED
        def stop(self):
            pass
        def close(self):
            pass

    dummy = DummyKazooClient("", 0)
    monkeypatch.setattr(zk, "KazooClient", lambda *a, **kw: dummy)
    monkeypatch.setattr(zk, "_client", None)

    with pytest.raises(Exception):
        zk.get_client()
    assert zk._client is None

    client = zk.get_client()
    assert client.state == KazooState.CONNECTED
    assert calls["start"] == 2
