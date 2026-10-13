import asyncio
import re
from typing import Literal

import rich
import typer
from rich.table import Table

from solradm.commands.kube.subapp import app
from solradm.config.util import get_current_context
from solradm.kube.utils import find_pods_by_label, get_kube_context_info, run_command_in_pod

@app.command(help="Show /var/solr/data disk usage for pods matching the solr-cloud label")
async def disk(
        solr_cloud: str = typer.Argument(..., help='Exact value of the "solr-cloud" pod label'),
        sort_by: Literal["used", "pct-used"] = typer.Option(
            "used",
            "--sort-by",
            help="Sort ascending by absolute used space (used) or by percentage used (pct-used)",
        ),
) -> None:
    """Display disk usage of /var/solr for pods matching the given solr-cloud label value."""

    kube = get_kube_context_info(get_current_context())
    pods = find_pods_by_label(kube, "solr-cloud", solr_cloud)

    if not pods:
        raise typer.BadParameter('No pods matched label selector "solr-cloud=<value>"')

    table = Table(title="Disk usage", header_style="bold magenta")
    table.add_column("Pod")
    table.add_column("Size", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Avail", justify="right")
    table.add_column("Use%", justify="right")

    async def _df(pod_name: str) -> tuple[str, str]:
        output = await asyncio.to_thread(run_command_in_pod, kube, pod_name, "df -h /var/solr/data")
        return pod_name, output

    results = await asyncio.gather(*(_df(p.metadata.name) for p in pods))

    def _parse_size(s: str) -> float:
        units = {"K": 1, "M": 2, "G": 3, "T": 4, "P": 5}
        try:
            num = float(re.sub("[^0-9.]+", "", s))
            unit = re.sub("[0-9.]+", "", s).strip().upper()[:1]
            return num * (1024 ** units.get(unit, 0))
        except Exception:
            return 0

    def _parse_pct(s: str) -> float:
        try:
            return float(s.replace("%", ""))
        except Exception:
            return 0

    rows = []
    for pod_name, output in results:
        lines = [l for l in output.strip().splitlines() if l]
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5:
                size, used, avail, pct = parts[1:5]
                rows.append((pod_name, size, used, avail, pct, _parse_size(used), _parse_pct(pct)))
                continue
        rows.append((pod_name, "-", "-", "-", "-", 0, 0))

    sort_index = 6 if sort_by == "pct-used" else 5
    rows.sort(key=lambda r: r[sort_index])
    for pod_name, size, used, avail, pct, _, _ in rows:
        table.add_row(pod_name, size, used, avail, pct)

    rich.print(table)
