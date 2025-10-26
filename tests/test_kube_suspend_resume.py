import importlib
import json
from types import SimpleNamespace

import pytest
import typer


def _reload_kube(monkeypatch, tmp_path):
    from solradm.commands import kube as kube_module

    importlib.reload(kube_module)

    monkeypatch.setattr(kube_module, "_ensure_state_dir", lambda: tmp_path)

    return kube_module


def test_suspend_requires_existing_kubecontext(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    monkeypatch.setattr(kube_module, "get_kubecontext", lambda name: None)

    with pytest.raises(typer.BadParameter):
        kube_module.suspend(kubecontext="missing", name_regex=".*")


def test_suspend_writes_state_per_kubecontext(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    context_data = {"name": "demo/one", "context": {"namespace": "demo"}}
    monkeypatch.setattr(kube_module, "get_kubecontext", lambda name: context_data)
    monkeypatch.setattr(kube_module, "switch_current_kubecontext", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube_module.Confirm, "ask", lambda *args, **kwargs: True)

    deployments = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="dep"),
            spec=SimpleNamespace(replicas=2),
        )
    ]
    statefulsets = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="sts"),
            spec=SimpleNamespace(replicas=3),
        )
    ]
    monkeypatch.setattr(kube_module, "_get_workloads", lambda pattern: (deployments, statefulsets))

    calls = []

    class DummyApps:
        def patch_namespaced_deployment_scale(self, name, namespace, body):
            calls.append(("dep", name, namespace, body))

        def patch_namespaced_stateful_set_scale(self, name, namespace, body):
            calls.append(("sts", name, namespace, body))

    monkeypatch.setattr(kube_module, "AppsV1Api", lambda: DummyApps())

    kube_module.suspend(kubecontext="demo/one", name_regex=".*", state_file=None)

    state_path = kube_module._state_file_for_context("demo/one")
    assert state_path.exists()

    with open(state_path) as fh:
        saved = json.load(fh)

    assert saved == {
        "deployments": {"dep": 2},
        "statefulsets": {"sts": 3},
    }

    assert calls == [
        ("dep", "dep", "demo", {"spec": {"replicas": 0}}),
        ("sts", "sts", "demo", {"spec": {"replicas": 0}}),
    ]

    with pytest.raises(typer.Exit) as exc_info:
        kube_module.suspend(kubecontext="demo/one", name_regex=".*", state_file=None)

    assert exc_info.value.exit_code == 1


def test_resume_restores_and_deletes_state(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    context_data = {"name": "demo", "context": {"namespace": "demo"}}
    monkeypatch.setattr(kube_module, "get_kubecontext", lambda name: context_data)
    monkeypatch.setattr(kube_module, "switch_current_kubecontext", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube_module.Confirm, "ask", lambda *args, **kwargs: True)

    state_path = kube_module._state_file_for_context("demo")
    with open(state_path, "w") as fh:
        json.dump({
            "deployments": {"dep": 2},
            "statefulsets": {"sts": 3},
        }, fh)

    calls = []

    class DummyApps:
        def patch_namespaced_deployment_scale(self, name, namespace, body):
            calls.append(("dep", name, namespace, body))

        def patch_namespaced_stateful_set_scale(self, name, namespace, body):
            calls.append(("sts", name, namespace, body))

    monkeypatch.setattr(kube_module, "AppsV1Api", lambda: DummyApps())

    kube_module.resume(kubecontext="demo", state_file=None)

    assert not state_path.exists()

    assert calls == [
        ("dep", "dep", "demo", {"spec": {"replicas": 2}}),
        ("sts", "sts", "demo", {"spec": {"replicas": 3}}),
    ]


def test_dir_opens_state_directory(monkeypatch, tmp_path):
    kube_module = _reload_kube(monkeypatch, tmp_path)

    launched = {}
    monkeypatch.setattr(kube_module.typer, "launch", lambda path: launched.setdefault("path", path))

    kube_module.dir()

    assert launched["path"] == str(tmp_path)
