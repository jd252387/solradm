import asyncio
import re
from itertools import cycle

import typer
from kubernetes.client import CoreV1Api
from rich.console import Console

from solradm.commands.kube.subapp import app
from solradm.completion.kube import container_names, pod_names
from solradm.completion.nodes import node_names
from solradm.config.util import get_current_context
from solradm.kube.utils import find_pods, find_pods_by_node_name, get_kube_context_info

@app.command(help="Stream logs for matching pods")
async def logs(
        pattern: str = typer.Argument(..., help="Regex of pod or node name", autocompletion=pod_names),
        node: bool = typer.Option(False, "--node", help="Treat pattern as node name", autocompletion=node_names),
        container: str | None = typer.Option(None, "--container", "-c", help="Container name",
                                             autocompletion=container_names),
) -> None:
    """Stream Kubernetes logs from pods matching PATTERN."""
    kube = get_kube_context_info(get_current_context())
    pods = (
        find_pods_by_node_name(kube, pattern)
        if node
        else find_pods(kube, re.compile(pattern))
    )

    if not pods:
        raise typer.BadParameter("No pods matched the given pattern")

    namespace = kube.namespace

    console = Console()
    color_cycle = cycle(["red", "green", "yellow", "blue", "magenta", "cyan"])
    pod_colors = {p.metadata.name: next(color_cycle) for p in pods}

    def _stream_logs(pod_name: str) -> None:
        resp = CoreV1Api(kube.api_client).read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            follow=True,
            _preload_content=False,
        )
        for line in resp.stream():
            console.print(f"[{pod_colors[pod_name]}]{pod_name}[/] {line.decode().rstrip()}")

    await asyncio.gather(
        *(asyncio.to_thread(_stream_logs, p.metadata.name) for p in pods)
    )
