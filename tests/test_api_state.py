import importlib
import json
import sys
from pathlib import Path

from kazoo.exceptions import NoNodeError


def _import_state_module(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from platformdirs import user_config_dir

    config_path = Path(user_config_dir("solradm", "eclipse")) / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("contexts: {available: [], current: {name: default}}\n")

    for module in ["solradm.config", "solradm.api", "solradm.api.state"]:
        sys.modules.pop(module, None)

    return importlib.import_module("solradm.api.state")


class FakeZkClient:
    def __init__(self, children, states):
        self._children = children
        self._states = states

    def get_children(self, path):
        if path == "/collections":
            return self._children
        raise AssertionError(f"Unexpected get_children path: {path}")

    def get(self, path):
        if path not in self._states:
            raise NoNodeError(path)

        collection = path.split("/")[-2]
        payload = json.dumps({collection: self._states[path]}).encode("utf-8")
        return payload, None


def test_get_collections_ignores_collections_without_state_json(monkeypatch, tmp_path):
    state = _import_state_module(monkeypatch, tmp_path)

    zk = FakeZkClient(
        children=["alpha", "ephemeral", "beta"],
        states={
            "/collections/alpha/state.json": {
                "pullReplicas": 0,
                "configName": "cfg",
                "replicationFactor": 1,
                "router": {"name": "compositeId"},
                "nrtReplicas": 1,
                "tlogReplicas": 0,
                "shards": {},
            },
            "/collections/beta/state.json": {
                "pullReplicas": 0,
                "configName": "cfg",
                "replicationFactor": 1,
                "router": {"name": "compositeId"},
                "nrtReplicas": 1,
                "tlogReplicas": 0,
                "shards": {},
            },
        },
    )

    monkeypatch.setattr(state, "get_client", lambda: zk)

    collections = state.get_collections()

    assert [collection.name for collection in collections] == ["alpha", "beta"]


def test_get_collections_raises_non_no_node_errors(monkeypatch, tmp_path):
    state = _import_state_module(monkeypatch, tmp_path)

    class ExplodingClient(FakeZkClient):
        def get(self, path):
            raise RuntimeError("boom")

    monkeypatch.setattr(state, "get_client", lambda: ExplodingClient(["alpha"], {}))

    try:
        state.get_collections()
        assert False, "Expected RuntimeError to be re-raised"
    except RuntimeError as exc:
        assert str(exc) == "boom"
