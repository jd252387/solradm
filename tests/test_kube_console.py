import importlib


def test_kube_console(monkeypatch, tmp_path):
    monkeypatch.setenv("SOLRADM_AUTO_CONFIG", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from solradm.commands import kube as kube_module
    importlib.reload(kube_module)

    monkeypatch.setattr(kube_module, "load_configured_kubecontext", lambda: None)
    monkeypatch.setattr(kube_module, "get_current_kubecontext_namespace", lambda: "demo")
    monkeypatch.setattr(kube_module, "is_openshift_cluster", lambda: True)

    class DummyApiClient:
        def call_api(self, *args, **kwargs):
            return None

    class DummyCOApi:
        def __init__(self, api_client=None):
            pass

        def get_namespaced_custom_object(self, group, version, namespace, plural, name):
            return {"spec": {"host": "console.example.com"}}

    monkeypatch.setattr(kube_module.client, "ApiClient", DummyApiClient)
    monkeypatch.setattr(kube_module.client, "CustomObjectsApi", DummyCOApi)

    opened = {}
    monkeypatch.setattr(kube_module.webbrowser, "open", lambda url: opened.setdefault("url", url))

    kube_module.console()

    assert opened["url"] == "https://console.example.com/k8s/ns/demo"
