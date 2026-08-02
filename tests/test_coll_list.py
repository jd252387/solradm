import os
import tempfile
from pathlib import Path

_test_config_home = Path(tempfile.gettempdir()) / "solradm-test-config"
(_test_config_home / "solradm").mkdir(parents=True, exist_ok=True)
settings_file = _test_config_home / "solradm" / "settings.yaml"
if not settings_file.exists():
    settings_file.write_text("contexts:\n  available: []\n  current: {name: default}\n")

os.environ.setdefault("XDG_CONFIG_HOME", str(_test_config_home))

from solradm.api import state
from solradm.api.models import Collection, Router
from solradm.commands.collections import list_collections as list_module


def _collection(name: str, config: str = "myconf") -> Collection:
    return Collection(
        name=name,
        pullReplicas=0,
        configName=config,
        replicationFactor=1,
        router=Router(name="compositeId", field=None),
        nrtReplicas=1,
        tlogReplicas=0,
        shards=[],
    )


def _render(cluster_state, aliases, monkeypatch, capsys):
    monkeypatch.setattr(list_module, "get_aliases", lambda: aliases)
    # collection=None must be passed explicitly: an unpassed Typer option stays a
    # truthy OptionInfo and would send execution down the regex branch.
    list_module.list_collections(cluster_state=cluster_state, collection=None)
    return capsys.readouterr().out


def test_list_shows_aliases_and_config(monkeypatch, capsys):
    out = _render(
        [_collection("logs", "logconf"), _collection("orphan")],
        {"logs": ["logs_read", "logs_write"]},
        monkeypatch,
        capsys,
    )

    assert "logs_read, logs_write" in out
    assert "logconf" in out
    # Collection with no alias gets the placeholder.
    assert "-" in out.split("orphan")[1]


def test_list_filters_by_regex(monkeypatch, capsys):
    monkeypatch.setattr(list_module, "get_aliases", lambda: {})
    list_module.list_collections(
        cluster_state=[_collection("logs-app"), _collection("metrics")],
        collection="^logs-",
    )
    out = capsys.readouterr().out

    assert "logs-app" in out
    assert "metrics" not in out


def test_get_aliases_inverts_zk_payload(monkeypatch):
    payload = b'{"collection": {"read": "a,b", "write": "a"}, "properties": {}}'
    monkeypatch.setattr(state, "get_client", lambda: type("_ZK", (), {"get": lambda self, p: (payload, None)})())

    assert state.get_aliases() == {"a": ["read", "write"], "b": ["read"]}


def test_get_aliases_handles_empty_znode(monkeypatch):
    # A cluster that has never had an alias: the znode exists, but with no data.
    monkeypatch.setattr(state, "get_client", lambda: type("_ZK", (), {"get": lambda self, p: (None, None)})())

    assert state.get_aliases() == {}
