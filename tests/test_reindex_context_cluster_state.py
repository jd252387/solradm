import asyncio
import importlib
import sys
from pathlib import Path


def _prepare_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from platformdirs import user_config_dir

    config_path = Path(user_config_dir("solradm", "eclipse")) / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "contexts: {available: [{name: default, zk: localhost:2181}, {name: source, zk: source:2181}, {name: target, zk: target:2181}], current: {name: default}}\n"
    )


def _load_modules(monkeypatch, tmp_path):
    _prepare_config(monkeypatch, tmp_path)
    for module in [
        "solradm.config",
        "solradm.commands.collections.reindex_ui",
        "solradm.commands.collections.reindex",
    ]:
        sys.modules.pop(module, None)

    reindex = importlib.import_module("solradm.commands.collections.reindex")
    models = importlib.import_module("solradm.api.models")
    return reindex, models


def _collection(models, name: str):
    replica = models.Replica(
        name=f"{name}_replica",
        core=f"{name}_core",
        node_name="node1",
        type="NRT",
        state="active",
        leader=True,
        force_set_state=False,
        base_url="http://localhost:8983/solr",
    )
    shard = models.Shard(name="shard1", range="0-0", replicas=[replica])
    return models.Collection(
        name=name,
        pullReplicas=0,
        configName="cfg",
        replicationFactor=1,
        router=models.Router(name="compositeId"),
        nrtReplicas=1,
        tlogReplicas=0,
        shards=[shard],
    )


def test_reindex_skips_current_cluster_state_fetch_when_both_contexts_set(monkeypatch, tmp_path):
    reindex, models = _load_modules(monkeypatch, tmp_path)

    def fail_get_collections():
        raise AssertionError("current context cluster state should not be fetched")

    resolved = {
        "source": _collection(models, "source_coll"),
        "target": _collection(models, "target_coll"),
    }

    monkeypatch.setattr("solradm.commands.filters.utils.get_collections", fail_get_collections)
    monkeypatch.setattr(
        reindex,
        "_resolve_collection",
        lambda collection_name, **kwargs: resolved["source" if collection_name == "source_coll" else "target"],
    )
    monkeypatch.setattr(reindex, "_detect_busy_shards", lambda leaders, handler: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(reindex, "_prompt_launch_when_idle", lambda: None)

    class FakeEngine:
        def __init__(self, shard_map, leaders, config):
            self.shard_map = shard_map
            self.leaders = leaders
            self.config = config

        def get_summary(self):
            return type("Summary", (), {"failures": [], "failed_targets": 0, "completed_targets": 1, "total_docs_processed": 0})()

    class FakeApp:
        def __init__(self, engine):
            self.engine = engine

        async def run_async(self):
            return None

    monkeypatch.setattr(reindex, "ReindexEngine", FakeEngine)
    monkeypatch.setattr(reindex, "ReindexApp", FakeApp)

    asyncio.run(
        reindex.reindex(
            source_collection="source_coll",
            target_collection="target_coll",
            source_context="source",
            source_zk=None,
            target_context="target",
            target_zk=None,
            handler="/dataimport",
            fq=None,
            shards=None,
            all_shards=True,
            rows=None,
            sort="first_timestamp asc, item_id asc",
            qt="/dih",
            fl="*,ignored_tmp1:_version_",
            timeout=300,
            commit=False,
            optimize=False,
        )
    )
