import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace


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


class FakeTable:
    def __init__(self, *args, **kwargs):
        self.rows = []

    def add_column(self, *args, **kwargs):
        return None

    def add_row(self, *cells):
        self.rows.append(cells)


def test_disk_sorts_ascending_by_used(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

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


def test_disk_sorts_ascending_by_percentage_used(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

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
