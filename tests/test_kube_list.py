from types import SimpleNamespace

from typer.testing import CliRunner


def _container(name, image, requests=None, limits=None, resources=True):
    resources = SimpleNamespace(requests=requests, limits=limits) if resources else None
    return SimpleNamespace(name=name, image=image, resources=resources)


def _owner_ref(kind, name, controller=True):
    return SimpleNamespace(kind=kind, name=name, controller=controller)


def _claim_template(name, size):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": size})),
    )


def _pvc(name, capacity=None, requested=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": requested} if requested else None)),
        status=SimpleNamespace(capacity={"storage": capacity} if capacity else None),
    )


def _statefulset(name, containers, ready=3, replicas=3, owners=None, claim_templates=None, volumes=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, owner_references=owners),
        spec=SimpleNamespace(
            replicas=replicas,
            volume_claim_templates=claim_templates,
            template=SimpleNamespace(spec=SimpleNamespace(containers=containers, volumes=volumes)),
        ),
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


def _patch(monkeypatch, kube_module, list_fn, pvcs=()):
    kube = SimpleNamespace(api_client=object(), namespace="solr")
    monkeypatch.setattr(kube_module, "get_kube_context_info", lambda *_: kube)
    monkeypatch.setattr(kube_module, "get_current_context", lambda: SimpleNamespace())
    monkeypatch.setattr(kube_module, "AppsV1Api", lambda _c: SimpleNamespace(list_namespaced_stateful_set=list_fn))
    monkeypatch.setattr(kube_module, "CoreV1Api", lambda _c: SimpleNamespace(
        list_namespaced_persistent_volume_claim=lambda _ns: SimpleNamespace(items=list(pvcs))))

    tables = []
    monkeypatch.setattr(kube_module, "Table", lambda *a, **k: tables.append(FakeTable()) or tables[-1])
    return tables


def test_multi_container_cells_are_newline_joined(monkeypatch, kube_modules):
    kube_module = kube_modules.list_resources

    items = [
        _statefulset(
            "solr-cloud",
            [
                _container("solrcloud", "solr:9.9",
                           {"cpu": "1", "memory": "4Gi", "ephemeral-storage": "2Gi"},
                           {"cpu": "2", "memory": "8Gi", "ephemeral-storage": "10Gi"}),
                _container("exporter", "exporter:1.4", resources=False),
            ],
        )
    ]
    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=items))

    result = CliRunner().invoke(kube_modules.app, ["list", "statefulsets"])
    assert result.exit_code == 0, result.output

    name, owner, pods, containers, images, cpu, memory, ephemeral, pvcs = tables[0].rows[0]
    assert (name, owner, pods) == ("solr-cloud", "-", "3/3")
    assert containers == "solrcloud\nexporter"
    assert images == "solr:9.9\nexporter:1.4"
    assert cpu == "1/2\n-/-"
    assert memory == "4Gi/8Gi\n-/-"
    assert ephemeral == "2Gi/10Gi\n-/-"
    assert pvcs == "-"


def test_label_selectors_are_unioned_and_deduped(monkeypatch, kube_modules):
    kube_module = kube_modules.list_resources

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

    result = CliRunner().invoke(kube_modules.app, ["list", "statefulsets", "-l", "app=solr", "-l", "tier=data"])
    assert result.exit_code == 0, result.output

    assert [row[0] for row in tables[0].rows] == ["solr-cloud", "zookeeper"]


def test_pattern_filters_by_name(monkeypatch, kube_modules):
    kube_module = kube_modules.list_resources

    items = [
        _statefulset("solr-cloud", [_container("solrcloud", "solr:9.9")]),
        _statefulset("zookeeper", [_container("zk", "zookeeper:3.9")]),
    ]
    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=items))

    result = CliRunner().invoke(kube_modules.app, ["list", "statefulsets", "-p", "^solr"])
    assert result.exit_code == 0, result.output

    assert [row[0] for row in tables[0].rows] == ["solr-cloud"]


def test_empty_result_prints_notice_and_no_table(monkeypatch, kube_modules):
    kube_module = kube_modules.list_resources

    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=[]))

    result = CliRunner().invoke(kube_modules.app, ["list", "statefulsets"])
    assert result.exit_code == 0, result.output
    assert tables[0].rows == []
    assert "No statefulsets found" in result.output


def test_owner_column_shows_controller_first(monkeypatch, kube_modules):
    """An operator-managed statefulset should name the CR that owns it.

    Mirrors the OpenShift case: a SolrCloud CR manages the Solr statefulset.
    A non-controller owner is still listed, but after the controller.
    """
    kube_module = kube_modules.list_resources

    items = [
        _statefulset("solr-cloud", [_container("solrcloud", "solr:9.9")],
                     owners=[_owner_ref("SolrCloud", "solr-cloud")]),
        _statefulset("shared", [_container("c", "i")],
                     owners=[_owner_ref("SolrCloud", "cr", controller=False),
                             _owner_ref("StatefulSet", "owner")]),
        _statefulset("orphan", [_container("c", "i")]),
    ]
    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=items))

    result = CliRunner().invoke(kube_modules.app, ["list", "statefulsets"])
    assert result.exit_code == 0, result.output

    owners = {row[0]: row[1] for row in tables[0].rows}
    assert owners["solr-cloud"] == "SolrCloud/solr-cloud"
    assert owners["shared"] == "StatefulSet/owner\nSolrCloud/cr"
    assert owners["orphan"] == "-"


def test_pvc_column_shows_templates_and_resolved_claims(monkeypatch, kube_modules):
    """StatefulSets show the volumeClaimTemplate; direct claims resolve to a real size.

    Template sizes come from the template's own request, since no PVC object
    exists for it. Directly-referenced claims are looked up in the PVC list,
    preferring bound capacity and falling back to the request. A claim that is
    not there at all renders `?`.
    """
    kube_module = kube_modules.list_resources

    items = [
        _statefulset("templated", [_container("c", "i")],
                     claim_templates=[_claim_template("data", "100Gi")]),
        _statefulset("bound", [_container("c", "i")],
                     volumes=[SimpleNamespace(persistent_volume_claim=SimpleNamespace(claim_name="claim-bound"))]),
        _statefulset("unbound", [_container("c", "i")],
                     volumes=[SimpleNamespace(persistent_volume_claim=SimpleNamespace(claim_name="claim-unbound"))]),
        _statefulset("dangling", [_container("c", "i")],
                     volumes=[SimpleNamespace(persistent_volume_claim=SimpleNamespace(claim_name="nope"))]),
        _statefulset("nonclaim", [_container("c", "i")],
                     volumes=[SimpleNamespace(persistent_volume_claim=None)]),
    ]
    pvcs = [_pvc("claim-bound", capacity="5Gi"), _pvc("claim-unbound", requested="7Gi")]
    tables = _patch(monkeypatch, kube_module, lambda _ns, **_kw: SimpleNamespace(items=items), pvcs)

    result = CliRunner().invoke(kube_modules.app, ["list", "statefulsets"])
    assert result.exit_code == 0, result.output

    claims = {row[0]: row[-1] for row in tables[0].rows}
    assert claims["templated"] == "data (100Gi)"
    assert claims["bound"] == "claim-bound (5Gi)"
    assert claims["unbound"] == "claim-unbound (7Gi)"   # falls back to the request
    assert claims["dangling"] == "nope (?)"
    assert claims["nonclaim"] == "-"                     # non-PVC volumes are skipped
