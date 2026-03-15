import asyncio

from solradm.api.models import Replica, Shard
from solradm.commands.collections.reindex_engine import ReindexConfig, ReindexEngine


def _source_shard() -> Shard:
    return Shard(
        name="shard1",
        range="80000000-ffffffff",
        replicas=[
            Replica(
                name="source_replica",
                core="source_core",
                node_name="node1",
                type="NRT",
                state="active",
                leader=True,
                force_set_state=False,
                base_url="http://source:8983/solr",
            )
        ],
    )


def _target_leader() -> Replica:
    return Replica(
        name="target_replica",
        core="target_core",
        node_name="node2",
        type="NRT",
        state="active",
        leader=True,
        force_set_state=False,
        base_url="http://target:8983/solr",
    )


def _build_engine(rows: int | None, commit: bool = False, optimize: bool = False) -> ReindexEngine:
    shard = _source_shard()
    shard_map = {"target_shard1": [shard]}
    leaders = {"target_shard1": _target_leader()}
    config = ReindexConfig(
        source_collection="source_collection",
        target_collection="target_collection",
        handler="/dataimport",
        fq=None,
        rows=rows,
        sort="id asc",
        qt="/dih",
        fl="*",
        timeout=60,
        commit=commit,
        optimize=optimize,
    )
    return ReindexEngine(shard_map, leaders, config)


def test_reindex_sends_rows_when_specified(monkeypatch):
    engine = _build_engine(rows=2000)
    captured_full_import_params = {}

    async def fake_send_request(base_url, path, params):
        if params.get("q") == "*:*":
            return {"response": {"numFound": 1}}
        if params.get("command") == "full-import":
            captured_full_import_params.update(params)
            return {"status": "busy"}
        if params.get("command") == "status":
            return {"status": "idle", "statusMessages": {}}
        raise AssertionError(f"unexpected request params: {params}")

    monkeypatch.setattr("solradm.commands.collections.reindex_engine.send_request", fake_send_request)

    asyncio.run(engine.run())

    assert captured_full_import_params["rows"] == 2000


def test_reindex_omits_rows_when_not_specified(monkeypatch):
    engine = _build_engine(rows=None)
    captured_full_import_params = {}

    async def fake_send_request(base_url, path, params):
        if params.get("q") == "*:*":
            return {"response": {"numFound": 1}}
        if params.get("command") == "full-import":
            captured_full_import_params.update(params)
            return {"status": "busy"}
        if params.get("command") == "status":
            return {"status": "idle", "statusMessages": {}}
        raise AssertionError(f"unexpected request params: {params}")

    monkeypatch.setattr("solradm.commands.collections.reindex_engine.send_request", fake_send_request)

    asyncio.run(engine.run())

    assert "rows" not in captured_full_import_params


def test_reindex_uses_commit_and_optimize_flags(monkeypatch):
    engine = _build_engine(rows=100, commit=True, optimize=True)
    captured_full_import_params = {}

    async def fake_send_request(base_url, path, params):
        if params.get("q") == "*:*":
            return {"response": {"numFound": 1}}
        if params.get("command") == "full-import":
            captured_full_import_params.update(params)
            return {"status": "busy"}
        if params.get("command") == "status":
            return {"status": "idle", "statusMessages": {}}
        raise AssertionError(f"unexpected request params: {params}")

    monkeypatch.setattr("solradm.commands.collections.reindex_engine.send_request", fake_send_request)

    asyncio.run(engine.run())

    assert captured_full_import_params["commit"] == "true"
    assert captured_full_import_params["optimize"] == "true"


def test_reindex_defaults_commit_and_optimize_to_false(monkeypatch):
    engine = _build_engine(rows=100)
    captured_full_import_params = {}

    async def fake_send_request(base_url, path, params):
        if params.get("q") == "*:*":
            return {"response": {"numFound": 1}}
        if params.get("command") == "full-import":
            captured_full_import_params.update(params)
            return {"status": "busy"}
        if params.get("command") == "status":
            return {"status": "idle", "statusMessages": {}}
        raise AssertionError(f"unexpected request params: {params}")

    monkeypatch.setattr("solradm.commands.collections.reindex_engine.send_request", fake_send_request)

    asyncio.run(engine.run())

    assert captured_full_import_params["commit"] == "false"
    assert captured_full_import_params["optimize"] == "false"
