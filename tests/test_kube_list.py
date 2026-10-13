import importlib
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner


def _reload_kube(monkeypatch, tmp_path):
    from platformdirs import user_config_dir

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_path = Path(user_config_dir("solradm", "eclipse")) / "settings.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("contexts:\n  available: []\n  current: {}\n")

    from solradm.commands import kube as kube_module

    importlib.reload(kube_module)
    return kube_module


def _container(name, image, requests=None, limits=None, resources=True):
    resources = SimpleNamespace(requests=requests, limits=limits) if resources else None
    return SimpleNamespace(name=name, image=image, resources=resources)


def _statefulset(name, containers, ready=3, replicas=3):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(replicas=replicas, template=SimpleNamespace(spec=SimpleNamespace(containers=containers))),
        status=SimpleNamespace(ready_replicas=ready),
    )


class FakeTable:
    def __init__(self, *args, **kwargs):
        self.rows = []

    def add_column(self, *args, **kwargs):
        return None

    def add_row(self, *cells):
        self.rows.append(cells)

    @property
    def row_count(self):
        return len(self.rows)


def _patch(monkeypatch, kube_module, list_fn):
    kube = SimpleNamespace(api_client=object(), namespace="solr")
    monkeypatch.setattr(kube_module, "get_kube_context_info", lambda *_: kube)
    monkeypatch.setattr(kube_module, "get_current_context", lambda: SimpleNamespace())
    monkeypatch.setattr(kube_module, "AppsV1Api", lambda _c: SimpleNamespace(list_namespaced_stateful_set=list_fn))

    tables = []
    monkeypatch.setattr(kube_module, "Table", lambda *a, **k: tables.append(FakeTable()) or tables[-1])
    return tables


def test_multi_container_cells_are_newline_joined(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    items = [
        _statefulset(
            "solr-cloud",
            [
                _container("solrcloud", "solr:9.9", {"cpu": "1", "memory": "4Gi"}, {"cpu": "2", "memory": "8Gi"}),
                _container("exporter", "exporter:1.4", resources=False),
            ],
        )
    ]
    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=items))

    result = CliRunner().invoke(kube_module.app, ["list", "statefulsets"])
    assert result.exit_code == 0, result.output

    name, pods, containers, images, cpu, memory = tables[0].rows[0]
    assert (name, pods) == ("solr-cloud", "3/3")
    assert containers == "solrcloud\nexporter"
    assert images == "solr:9.9\nexporter:1.4"
    assert cpu == "1/2\n-/-"
    assert memory == "4Gi/8Gi\n-/-"


def test_label_selectors_are_unioned_and_deduped(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    by_selector = {
        "app=solr": [_statefulset("solr-cloud", [_container("solrcloud", "solr:9.9")])],
        "tier=data": [
            _statefulset("solr-cloud", [_container("solrcloud", "solr:9.9")]),
            _statefulset("zookeeper", [_container("zk", "zookeeper:3.9")]),
        ],
    }

    def list_fn(_ns, label_selector=None):
        return SimpleNamespace(items=by_selector[label_selector])

    tables = _patch(monkeypatch, kube_module, list_fn)

    result = CliRunner().invoke(kube_module.app, ["list", "statefulsets", "-l", "app=solr", "-l", "tier=data"])
    assert result.exit_code == 0, result.output

    assert [row[0] for row in tables[0].rows] == ["solr-cloud", "zookeeper"]


def test_pattern_filters_by_name(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    items = [
        _statefulset("solr-cloud", [_container("solrcloud", "solr:9.9")]),
        _statefulset("zookeeper", [_container("zk", "zookeeper:3.9")]),
    ]
    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=items))

    result = CliRunner().invoke(kube_module.app, ["list", "statefulsets", "-p", "^solr"])
    assert result.exit_code == 0, result.output

    assert [row[0] for row in tables[0].rows] == ["solr-cloud"]


def test_empty_result_prints_notice_and_no_table(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=[]))

    result = CliRunner().invoke(kube_module.app, ["list", "statefulsets"])
    assert result.exit_code == 0, result.output
    assert tables[0].rows == []
    assert "No statefulsets found" in result.output
