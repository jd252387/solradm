import asyncio
import re
from typing import List

import rich
import typer
from async_typer import AsyncTyper
from rich.table import Table

from solradm.kube.utils import find_pods, find_pods_by_node_name, run_command_in_pod

app = AsyncTyper()


@app.async_command(help="Show Solr pod disk usage for /var/solr")
async def disk(
        node: List[str] | None = typer.Option(None, "--node", help="Node names to inspect"),
        pattern: str = typer.Option("solr", "--pattern", help="Regex to match pod names"),
):
    pods = []
    if node:
        for n in node:
            pods.extend(find_pods_by_node_name(n))
    else:
        pods = find_pods(re.compile(pattern))

    if not pods:
        rich.print("[error] ❌  No pods found")
        raise typer.Exit(1)

    async def get_usage(pod):
        output = await asyncio.to_thread(run_command_in_pod, pod.metadata.name, "df -h /var/solr")
        lines = output.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[-1].split()
            used = parts[2]
            avail = parts[3]
            pct = parts[4]
        else:
            used = avail = pct = "n/a"
        return pod.metadata.name, used, avail, pct

    results = await asyncio.gather(*(get_usage(pod) for pod in pods))

    table = Table(title="Disk usage for /var/solr", header_style="bold magenta")
    table.add_column("Pod")
    table.add_column("Used", justify="right")
    table.add_column("Avail", justify="right")
    table.add_column("Use%", justify="right")

    for pod_name, used, avail, pct in results:
        table.add_row(pod_name, used, avail, pct)

    rich.print(table)
