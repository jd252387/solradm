import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from kubernetes.client import Configuration


@dataclass
class FakeKubeContextInfo:
    api_client: object
    name: str
    namespace: str


def test_suspend_requires_existing_kubecontext(monkeypatch, tmp_path, kube_modules):
    kube_module = kube_modules.suspend

    from solradm.exceptions.adm_exception import AdmException
    def raise_missing(ctx):
        raise AdmException(f"Kubecontext {ctx.kubecontext} could not be found in your kubeconfig.")

    monkeypatch.setattr(kube_module, "get_kube_context_info", raise_missing)

    with pytest.raises(AdmException):
        kube_module.suspend(kubecontext="missing", pattern=".*")





def test_suspend_sets_namespace_from_context(monkeypatch, tmp_path, kube_modules):
    kube_module = kube_modules.suspend

    fake_kube_info = FakeKubeContextInfo(api_client=None, name="demo/one", namespace="nondefault")
    monkeypatch.setattr(kube_module, "get_kube_context_info", lambda ctx: fake_kube_info)
    monkeypatch.setattr(kube_module.Confirm, "ask", lambda *args, **kwargs: True)

    deployments = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="dep"),
            spec=SimpleNamespace(replicas=1),
        )
    ]
    statefulsets = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="sts"),
            spec=SimpleNamespace(replicas=2),
        )
    ]

    class DummyApps:
        def __init__(self, api_client):
            self.list_calls = []
            self.patch_calls = []

        def list_namespaced_deployment(self, namespace):
            self.list_calls.append(("dep", namespace))
            return SimpleNamespace(items=deployments)

        def list_namespaced_stateful_set(self, namespace):
            self.list_calls.append(("sts", namespace))
            return SimpleNamespace(items=statefulsets)

        def patch_namespaced_deployment_scale(self, name, namespace, body):
            self.patch_calls.append(("dep", name, namespace, body))

        def patch_namespaced_stateful_set_scale(self, name, namespace, body):
            self.patch_calls.append(("sts", name, namespace, body))

    dummy_apps = DummyApps(None)
    monkeypatch.setattr(kube_module, "AppsV1Api", lambda api_client: dummy_apps)

    kube_module.suspend(kubecontext="demo/one", pattern=".*", state_file=None, dry=False)

    assert dummy_apps.list_calls == [("dep", "nondefault"), ("sts", "nondefault")]
    assert dummy_apps.patch_calls == [
        ("dep", "dep", "nondefault", {"spec": {"replicas": 0}}),
        ("sts", "sts", "nondefault", {"spec": {"replicas": 0}}),
    ]
    class DummyApps:
        def __init__(self, api_client):
            self.patch_calls = []

        def patch_namespaced_deployment_scale(self, name, namespace, body):
            self.patch_calls.append(("dep", name, namespace, body))

        def patch_namespaced_stateful_set_scale(self, name, namespace, body):
            self.patch_calls.append(("sts", name, namespace, body))

    dummy_apps = DummyApps(None)
    monkeypatch.setattr(kube_module, "AppsV1Api", lambda api_client: dummy_apps)

    monkeypatch.setattr(kube_modules.resume, "get_kube_context_info", lambda ctx: fake_kube_info)
    monkeypatch.setattr(kube_modules.resume.Confirm, "ask", lambda *args, **kwargs: True)
    monkeypatch.setattr(kube_modules.resume, "AppsV1Api", lambda api_client: dummy_apps)

    kube_modules.resume.resume(kubecontext="demo", state_file=None)

    assert dummy_apps.patch_calls == [
        ("dep", "dep", "nondefault", {"spec": {"replicas": 2}}),
        ("sts", "sts", "nondefault", {"spec": {"replicas": 3}}),
    ]


def test_resume_uses_current_context_state_file_when_unspecified(monkeypatch, tmp_path, kube_modules):
    kube_module = kube_modules.resume

    state_path = tmp_path / "demo.json"
    state_path.write_text(json.dumps({"deployments": {"dep": 2}, "statefulsets": {"sts": 3}}))

    fake_current = SimpleNamespace(kubecontext="demo")
    fake_kube_info = FakeKubeContextInfo(api_client=None, name="demo", namespace="nondefault")

    monkeypatch.setattr(kube_module, "get_current_context", lambda: fake_current)
    monkeypatch.setattr(kube_module, "get_kube_context_info", lambda ctx: fake_kube_info)
    monkeypatch.setattr(kube_module.Confirm, "ask", lambda *args, **kwargs: True)

    class DummyApps:
        def __init__(self, api_client):
            self.patch_calls = []

        def patch_namespaced_deployment_scale(self, name, namespace, body):
            self.patch_calls.append(("dep", name, namespace, body))

        def patch_namespaced_stateful_set_scale(self, name, namespace, body):
            self.patch_calls.append(("sts", name, namespace, body))

    dummy_apps = DummyApps(None)
    monkeypatch.setattr(kube_module, "AppsV1Api", lambda api_client: dummy_apps)

    kube_module.resume(kubecontext=None, state_file=None)

    assert dummy_apps.patch_calls == [
        ("dep", "dep", "nondefault", {"spec": {"replicas": 2}}),
        ("sts", "sts", "nondefault", {"spec": {"replicas": 3}}),
    ]
    assert not state_path.exists()


def test_dir_opens_state_directory(monkeypatch, tmp_path, kube_modules):
    kube_module = kube_modules.state_dir

    launched = {}
    monkeypatch.setattr(kube_module.typer, "launch", lambda path: launched.setdefault("path", path))

    kube_module.dir()

    assert launched["path"] == str(tmp_path)


def test_suspend_requires_exactly_one_selector(monkeypatch, tmp_path, kube_modules):
    kube_module = kube_modules.suspend

    with pytest.raises(typer.Exit):
        kube_module.suspend(kubecontext="demo", pattern=".*", label=["app=solr"])

    with pytest.raises(typer.Exit):
        kube_module.suspend(kubecontext="demo", pattern=None, label=None)
