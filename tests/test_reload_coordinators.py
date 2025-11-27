import asyncio
import datetime


def _collection(models, name: str, config: str):
    return models.Collection(
        name=name,
        pullReplicas=0,
        configName=config,
        replicationFactor=1,
        router=models.Router(name="implicit", field=None),
        nrtReplicas=0,
        tlogReplicas=0,
        shards=[],
    )


def _core(models, collection: str, name: str):
    return models.Core(
        name=name,
        instanceDir=f"/var/solr/data/{name}",
        dataDir=f"/var/solr/data/{name}/data",
        config="solrconfig.xml",
        schema="schema.xml",
        startTime=datetime.datetime.now(),
        uptime=1,
        lastPublished="active",
        configVersion=1,
        cloud=models.CoreCloudDescriptor(
            collection=collection, shard=None, replica=None, replicaType="NRT"
        ),
    )


def test_reload_filters_coordinator_cores_by_configset(monkeypatch, tmp_path):
    config_home = tmp_path / "cfg"
    settings_dir = config_home / "solradm"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.yaml").write_text(
        "contexts:\n  available: []\n  current: {name: default}\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    import solradm.commands.collections.maintenance as maintenance
    import solradm.commands.filters.utils as filters_utils
    import solradm.api.models as models

    cluster_state = [
        _collection(models, "alpha", "config-alpha"),
        _collection(models, "beta", "config-beta"),
    ]

    monkeypatch.setattr(filters_utils, "get_collections", lambda: cluster_state)
    monkeypatch.setattr(
        maintenance, "get_nodes_by_role", lambda role: {"on": ["coordinator1"]}
    )

    async def fake_get_cores_from_node(_):
        return [
            _core(models, "alpha", "alpha_core"),
            _core(models, "beta", "beta_core"),
        ]

    monkeypatch.setattr(
        maintenance.api_utils, "get_cores_from_node", fake_get_cores_from_node
    )

    sent_requests = []

    async def fake_send_request(host, endpoint, params=None, **_):
        sent_requests.append((host, endpoint, params))
        return {}

    monkeypatch.setattr(maintenance, "send_request", fake_send_request)

    asyncio.run(
        maintenance.reload(
            dry_run=False,
            collection_name_filter=r"^alpha$",
            coordinators=True,
            skip_checks=True,
        )
    )

    assert [params["core"] for _, _, params in sent_requests] == ["alpha_core"]
    assert all(host == "coordinator1" for host, _, _ in sent_requests)
