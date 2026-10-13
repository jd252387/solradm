import webbrowser

import rich
import typer
from kubernetes.client import CustomObjectsApi

from solradm.commands.kube.common import is_openshift_cluster
from solradm.commands.kube.subapp import app
from solradm.config.util import get_current_context
from solradm.kube.utils import get_kube_context_info

@app.command(help="Open OpenShift console for the current namespace")
def ui() -> None:
    """Open the OpenShift web console in a browser for the current namespace."""

    kube = get_kube_context_info(get_current_context())
    if not is_openshift_cluster(kube):
        rich.print("[error] ❌ The current Kubernetes cluster is not of the OpenShift distribution.")

    api = CustomObjectsApi(kube.api_client)
    namespace = kube.namespace

    try:
        route = api.get_namespaced_custom_object(
            group="route.openshift.io",
            version="v1",
            namespace="openshift-console",
            plural="routes",
            name="console",
        )
        host = route["spec"]["host"]
    except Exception as e:
        rich.print(f"[error] ❌ Unable to determine OpenShift console host - {e}")
        raise typer.Exit(1)

    url = f"https://{host}/k8s/ns/{namespace}/core~v1~Pod"
    webbrowser.open(url)
    rich.print(f"[success]✅  Opened OpenShift console at {url}")
