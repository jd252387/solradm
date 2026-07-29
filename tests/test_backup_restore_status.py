import asyncio
import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

_test_config_home = Path(tempfile.gettempdir()) / "solradm-test-config"
(_test_config_home / "solradm").mkdir(parents=True, exist_ok=True)
settings_file = _test_config_home / "solradm" / "settings.yaml"
if not settings_file.exists():
    settings_file.write_text("contexts:\n  available: []\n  current: {name: default}\n")

os.environ.setdefault("XDG_CONFIG_HOME", str(_test_config_home))

from solradm.api.models import Collection, Replica, Router, Shard
from solradm.commands import backups as backups_module

NAMESPACE = "myns"


def _node(index: int) -> str:
    return f"{NAMESPACE}-solrcloud-{index}.{NAMESPACE}-solrcloud-headless.{NAMESPACE}:8983_solr"


def _replica(name: str, core: str, node_name: str) -> Replica:
    return Replica(
        name=name,
        core=core,
        node_name=node_name,
        type="NRT",
        state="active",
        leader=False,
        force_set_state=False,
        base_url=f"http://{node_name}:8983/solr",
    )


def _cluster_state() -> list[Collection]:
    """Four cores over two nodes, so pod resolution has a duplicate node to collapse."""
    shards = [
        Shard(
            name=f"shard{i}",
            range=f"{i}00-{i}99",
            replicas=[_replica(f"core_node{i}", f"coll_shard{i}_replica_n{i}", _node(i % 2))],
        )
        for i in range(1, 5)
    ]
    return [
        Collection(
            name="coll",
            pullReplicas=0,
            configName="myconf",
            replicationFactor=1,
            router=Router(name="compositeId", field=None),
            nrtReplicas=1,
            tlogReplicas=0,
            shards=shards,
        )
    ]


class FakeTable:
    def __init__(self, *args, **kwargs):
        self.rows = []

    def add_column(self, *args, **kwargs):
        return None

    def add_row(self, *cells):
        self.rows.append(cells)


class Harness:
    """Stubs every outbound call restore_status makes, and counts the ones that matter."""

    def __init__(self, sizes, failing_core=None):
        self.sizes = sizes
        self.failing_core = failing_core
        self.overseer_calls = 0
        self.find_pods_calls = 0
        self.exec_calls = []
        self.max_concurrent = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self.tables = []

    def install(self, monkeypatch):
        monkeypatch.setattr(backups_module, "get_overseer_leader", self._overseer)
        monkeypatch.setattr(backups_module, "send_request", self._send_request)
        monkeypatch.setattr(backups_module, "get_current_context", lambda: SimpleNamespace())
        monkeypatch.setattr(
            backups_module,
            "get_kube_context_info",
            lambda *_: SimpleNamespace(namespace=NAMESPACE, api_client=None, name="ctx"),
        )
        monkeypatch.setattr(backups_module, "find_pods", self._find_pods)
        monkeypatch.setattr(backups_module, "run_command_in_pod", self._run_command_in_pod)
        monkeypatch.setattr(backups_module, "Table", lambda *a, **k: self._new_table())
        monkeypatch.setattr(backups_module.rich, "print", lambda *_a, **_k: None)

    def _new_table(self):
        self.tables.append(FakeTable())
        return self.tables[-1]

    def _overseer(self):
        self.overseer_calls += 1
        return "overseer:8983_solr"

    async def _send_request(self, _host, _endpoint, params=None, **_kwargs):
        assert params == {"command": "restorestatus"}
        return {"restorestatus": {"status": "In progress"}}

    def _find_pods(self, _kube, _pattern):
        self.find_pods_calls += 1
        return [
            SimpleNamespace(metadata=SimpleNamespace(name=f"{NAMESPACE}-solrcloud-{i}"))
            for i in range(2)
        ]

    def _run_command_in_pod(self, _kube, pod_name, command):
        core = command.split("/var/solr/data/")[1].split("/")[0]
        with self._lock:
            self.exec_calls.append(core)
            self._in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self._in_flight)
        try:
            time.sleep(0.05)
            if core == self.failing_core:
                raise RuntimeError("exec failed")
            return f"{self.sizes[core]} /var/solr/data/{core}/data/restore.20240101\n"
        finally:
            with self._lock:
                self._in_flight -= 1


def _run(monkeypatch, harness):
    harness.install(monkeypatch)
    asyncio.run(backups_module.restore_status(cluster_state=_cluster_state()))
    return harness.tables[0].rows


def test_restore_status_batches_lookups_and_runs_inspections_concurrently(monkeypatch):
    harness = Harness(
        sizes={
            "coll_shard1_replica_n1": 100,
            "coll_shard2_replica_n2": 300,
            "coll_shard3_replica_n3": 200,
            "coll_shard4_replica_n4": 400,
        }
    )
    rows = _run(monkeypatch, harness)

    # One ZooKeeper lookup and one pod listing for the whole run, not one per core.
    assert harness.overseer_calls == 1
    assert harness.find_pods_calls == 1

    # The du execs overlap instead of running back to back.
    assert harness.max_concurrent > 1

    assert [row[0] for row in rows] == [
        "coll_shard4_replica_n4",
        "coll_shard2_replica_n2",
        "coll_shard3_replica_n3",
        "coll_shard1_replica_n1",
    ]
    # Nodes still map to the right pods after matching moved out of the shared helper.
    assert [row[2] for row in rows] == [
        f"{NAMESPACE}-solrcloud-0",
        f"{NAMESPACE}-solrcloud-0",
        f"{NAMESPACE}-solrcloud-1",
        f"{NAMESPACE}-solrcloud-1",
    ]


def test_restore_status_failed_inspection_is_reported_once_without_retry(monkeypatch):
    failing = "coll_shard2_replica_n2"
    harness = Harness(
        sizes={
            "coll_shard1_replica_n1": 100,
            "coll_shard3_replica_n3": 200,
            "coll_shard4_replica_n4": 400,
        },
        failing_core=failing,
    )
    rows = _run(monkeypatch, harness)

    assert harness.exec_calls.count(failing) == 1

    # The core still shows up, sorted last with no size.
    assert [row[0] for row in rows][-1] == failing
    assert rows[-1][4] == "—"


def test_restore_status_reports_nothing_in_progress(monkeypatch):
    harness = Harness(sizes={})

    async def _idle(_host, _endpoint, params=None, **_kwargs):
        return {"restorestatus": {"status": "success"}}

    harness.install(monkeypatch)
    monkeypatch.setattr(backups_module, "send_request", _idle)
    asyncio.run(backups_module.restore_status(cluster_state=_cluster_state()))

    assert harness.exec_calls == []
    assert harness.find_pods_calls == 0
    assert harness.tables == []
