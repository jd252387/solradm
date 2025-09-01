import re
import sys

import typer
from async_typer import AsyncTyper
from kubernetes.client import CoreV1Api
from kubernetes.stream import stream

from solradm.kube.utils import (
    find_pods,
    find_pods_by_node_name,
    get_current_kubecontext_namespace,
)

app = AsyncTyper()


@app.async_command(help="Stream logs for matching pods")
async def logs(
    pattern: str = typer.Argument(..., help="Regex of pod or node name"),
    node: bool = typer.Option(False, "--node", help="Treat pattern as node name"),
    container: str | None = typer.Option(None, "--container", "-c", help="Container name"),
):
    """Stream Kubernetes logs from pods matching PATTERN."""

    pods = find_pods_by_node_name(pattern) if node else find_pods(re.compile(pattern))

    if not pods:
        raise typer.BadParameter("No pods matched the given pattern")

    namespace = get_current_kubecontext_namespace()

    for pod in pods:
        resp = stream(
            CoreV1Api().connect_get_namespaced_pod_log,
            pod.metadata.name,
            namespace,
            container=container,
            follow=True,
            _preload_content=False,
        )
        try:
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    out = resp.read_stdout()
                    if out:
                        print(out, end="")
                if resp.peek_stderr():
                    err = resp.read_stderr()
                    if err:
                        print(err, end="", file=sys.stderr)
        finally:
            resp.close()
