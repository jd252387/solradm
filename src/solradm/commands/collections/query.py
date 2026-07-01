from __future__ import annotations

import json
from typing import List

import rich
import typer
from rich import box
from rich.panel import Panel
from rich.table import Table

from solradm.api.state import get_nodes_by_role
from solradm.api.utils import send_request
from solradm.commands.collections.subapp import app
from solradm.completion.collections import collection_names
from solradm.zk.utils import get_overseer_leader


@app.command(help="Execute a query against a collection")
async def query(
    collection: str = typer.Argument(
        ..., help="Collection to query", autocompletion=collection_names
    ),
    q: str = typer.Argument(..., help="Lucene query string"),
    rows: int = typer.Option(10, help="Number of rows to return"),
    fl: str = typer.Option("*", help="Fields to return"),
    start: int = typer.Option(0, help="Starting offset"),
    fq: List[str] | None = typer.Option(None, "--fq", help="Filter query"),
    param: List[str] | None = typer.Option(
        None,
        "--param",
        "-p",
        help="Additional query parameter in the form name=value",
    ),
    debug: bool = typer.Option(False, help="Include debug information"),
) -> None:
    """Query a collection and pretty-print the top results."""

    params: dict = {"q": q, "rows": rows, "fl": fl, "start": start}
    if fq:
        params["fq"] = fq
    if param:
        for kv in param:
            if "=" not in kv:
                rich.print(f"[warning]Ignoring invalid param {kv!r}")
                continue
            k, v = kv.split("=", 1)
            if k in params:
                existing = params[k]
                if isinstance(existing, list):
                    existing.append(v)
                else:
                    params[k] = [existing, v]
            else:
                params[k] = v
    if debug:
        params["debug"] = "true"

    try:
        coordinators = get_nodes_by_role("coordinator").get("on", [])
    except Exception:
        coordinators = []
    base = coordinators[0] if coordinators else get_overseer_leader()

    resp = await send_request(base, f"/{collection}/select", params=params)

    docs = resp.get("response", {}).get("docs", [])
    if docs:
        if fl != "*":
            fields = [f.strip() for f in fl.split(",") if f.strip()]
        else:
            fields = sorted({key for doc in docs for key in doc.keys()})
        table = Table(
            title="Results",
            header_style="bold cyan",
            expand=True,
            box=box.SIMPLE_HEAVY,
            row_styles=["", "dim"],
        )
        for field in fields:
            table.add_column(field, style="green")
        for doc in docs:
            table.add_row(*[str(doc.get(field, "")) for field in fields])
        rich.print(table)
    else:
        rich.print(Panel("No documents found", style="yellow"))

    if debug and "debug" in resp:
        rich.print_json(data=json.dumps(resp["debug"]))
