import asyncio
from types import SimpleNamespace


class FakeTable:
    def __init__(self, *args, **kwargs):
        self.rows = []

    def add_column(self, *args, **kwargs):
        return None

    def add_row(self, *cells):
        self.rows.append(cells)


def test_disk_sorts_ascending_by_used(monkeypatch, kube_modules):
    kube_module = kube_modules.disk

    monkeypatch.setattr(kube_module, "get_kube_context_info", lambda *_: SimpleNamespace())
    monkeypatch.setattr(kube_module, "get_current_context", lambda: SimpleNamespace())
    monkeypatch.setattr(
        kube_module,
        "find_pods_by_label",
        lambda *_: [
            SimpleNamespace(metadata=SimpleNamespace(name="pod-a")),
            SimpleNamespace(metadata=SimpleNamespace(name="pod-b")),
        ],
    )

    outputs = {
        "pod-a": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda 10G 8G 2G 80% /var/solr/data\n",
        "pod-b": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda 10G 2G 8G 20% /var/solr/data\n",
    }
    monkeypatch.setattr(kube_module, "run_command_in_pod", lambda _k, pod_name, _cmd: outputs[pod_name])

    tables = []
    monkeypatch.setattr(kube_module, "Table", lambda *a, **k: tables.append(FakeTable()) or tables[-1])
    monkeypatch.setattr(kube_module.rich, "print", lambda *_: None)

    asyncio.run(kube_module.disk("cloud-a"))

    assert [row[0] for row in tables[0].rows] == ["pod-b", "pod-a"]


def test_disk_sorts_ascending_by_percentage_used(monkeypatch, kube_modules):
    kube_module = kube_modules.disk

    monkeypatch.setattr(kube_module, "get_kube_context_info", lambda *_: SimpleNamespace())
    monkeypatch.setattr(kube_module, "get_current_context", lambda: SimpleNamespace())
    monkeypatch.setattr(
        kube_module,
        "find_pods_by_label",
        lambda *_: [
            SimpleNamespace(metadata=SimpleNamespace(name="pod-a")),
            SimpleNamespace(metadata=SimpleNamespace(name="pod-b")),
            SimpleNamespace(metadata=SimpleNamespace(name="pod-c")),
        ],
    )

    outputs = {
        "pod-a": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda 10G 8G 2G 80% /var/solr/data\n",
        "pod-b": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda 10G 2G 8G 20% /var/solr/data\n",
        "pod-c": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda 100G 30G 70G 30% /var/solr/data\n",
    }
    monkeypatch.setattr(kube_module, "run_command_in_pod", lambda _k, pod_name, _cmd: outputs[pod_name])

    tables = []
    monkeypatch.setattr(kube_module, "Table", lambda *a, **k: tables.append(FakeTable()) or tables[-1])
    monkeypatch.setattr(kube_module.rich, "print", lambda *_: None)

    asyncio.run(kube_module.disk("cloud-a", sort_by="pct-used"))

    assert [row[0] for row in tables[0].rows] == ["pod-b", "pod-c", "pod-a"]
