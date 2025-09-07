import importlib


def test_kube_console(monkeypatch, tmp_path):
    cfg_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    settings_dir = cfg_home / "solradm"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.yaml").write_text("contexts:\n  current: {name: test}\n  available: []\n")

    from solradm.commands import kube as kube_module
    importlib.reload(kube_module)

    monkeypatch.setattr(kube_module, "load_configured_kubecontext", lambda: None)
    monkeypatch.setattr(kube_module, "get_current_kubecontext_namespace", lambda: "demo")

    class DummyApiClient:
        def call_api(self, *args, **kwargs):
            return None

    class DummyCOApi:
        def __init__(self, api_client):
            pass

        def get_namespaced_custom_object(self, group, version, namespace, plural, name):
            return {"spec": {"host": "console.example.com"}}

    monkeypatch.setattr(kube_module, "ApiClient", DummyApiClient)
    monkeypatch.setattr(kube_module, "CustomObjectsApi", DummyCOApi)

    opened = {}
    monkeypatch.setattr(kube_module.webbrowser, "open", lambda url: opened.setdefault("url", url))

    kube_module.console()

    assert opened["url"] == "https://console.example.com/k8s/ns/demo"
