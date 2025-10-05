import asyncio
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Sequence

import rich
import typer
from aiohttp import ContentTypeError
from async_typer import AsyncTyper
from kazoo.client import KazooClient
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Confirm
from rich.table import Table
from rich.panel import Panel
from rich import box

import solradm.api.utils as api_utils
from solradm.api import get_session
from solradm.api.models import Collection, Replica, Shard
from solradm.api.state import get_nodes_by_role, get_collections
from solradm.api.utils import validate_num_replicas, get_replicas, send_request, get_host_with_scheme
from solradm.commands.callbacks import add_verbosity_option
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.replica_position_filter import ReplicaPositionFilter
from solradm.commands.filters.replica_state_filter import ReplicaStateFilter
from solradm.commands.filters.replica_type_filter import ReplicaTypeFilter
from solradm.commands.filters.shard_filter import ShardFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.completion.collections import collection_names, source_collection_names
from solradm.completion.configs import config_names
from solradm.completion.contexts import context_names
from solradm.completion.nodes import node_names
from solradm.config import settings
from solradm.config.util import get_current_context
from solradm.renderers.task_table import MultiTaskTable
from solradm.tasks.metatask import MetaTask
from solradm.tasks.multimetatask import MultiMetaTask
from solradm.zk.utils import get_overseer_leader
from solradm.exceptions.solr_exception import SolrException

app = AsyncTyper()
add_verbosity_option(app)


def _compile_node_patterns(patterns: Sequence[str] | None, option_display: str) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise typer.BadParameter(
                f"Invalid regular expression for {option_display} '{pattern}': {exc}"
            ) from exc
    return compiled


def _select_nodes(
        available_nodes: Iterable[str],
        include_patterns: Sequence[str] | None,
        exclude_patterns: Sequence[str] | None,
) -> list[str]:
    nodes = list(available_nodes)
    include_regexes = _compile_node_patterns(include_patterns, "--node")
    exclude_regexes = _compile_node_patterns(exclude_patterns, "--exclude-node")

    def matches(node: str) -> bool:
        if include_regexes and not any(regex.search(node) for regex in include_regexes):
            return False
        if exclude_regexes and any(regex.search(node) for regex in exclude_regexes):
            return False
        return True

    return sorted({node for node in nodes if matches(node)})


@app.async_command(help=(
    "Remove replicas for filtered collections.\n\n"
    "Examples:\n"
    "  solradm coll depopulate --collection '^logs-' --replica-state down --dry\n"
    "  solradm coll depopulate --collection '^metrics$' --shards 1-3 --replica-type leader --node 'solr0[12]'\n"
    "  solradm coll depopulate --collection '^analytics$' --replica-position 2 --exclude-node 'solr-backup'"
))
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter, ReplicaTypeFilter, ReplicaStateFilter, ReplicaPositionFilter)
async def depopulate(
        cluster_state: List[Collection],
        node: List[str] | None = typer.Option(
            None,
            "--node",
            help="Regex to select nodes",
            autocompletion=node_names,
        ),
        exclude_node: List[str] | None = typer.Option(
            None,
            "--exclude-node",
            help="Regex to exclude nodes",
            autocompletion=node_names,
        ),
):
    """Remove replicas from the selected collections."""

    replicas = get_replicas(cluster_state)

    if node or exclude_node:
        selected_nodes = _select_nodes(
            [replica.node_name for replica in replicas if replica.node_name],
            node,
            exclude_node,
        )

        if not selected_nodes:
            rich.print("[error] ❌ No nodes match the given selectors")
            raise typer.Exit(1)

        filtered_collections: list[Collection] = []
        filtered_replicas: list[Replica] = []
        for coll in cluster_state:
            new_shards: list[Shard] = []
            for shard in coll.shards:
                new_replicas = [
                    replica for replica in shard.replicas if replica.node_name in selected_nodes
                ]
                if new_replicas:
                    shard.replicas = new_replicas
                    new_shards.append(shard)
                    filtered_replicas.extend(new_replicas)
            if new_shards:
                coll.shards = new_shards
                filtered_collections.append(coll)

        cluster_state = filtered_collections
        replicas = filtered_replicas

        if not replicas:
            rich.print("[error] ❌ No replicas match the given node selectors")
            raise typer.Exit(1)

    table = Table(title="Cluster State", header_style="bold magenta")
    table.add_column("Collection", style="cyan")
    table.add_column("Active Shards", justify="right", style="green")
    table.add_column("Active Replicas", justify="right", style="green")
    table.add_column("Problematic Shards", justify="right", style="yellow")
    table.add_column("Problematic Replicas", justify="right", style="yellow")

    total_active_shards = 0
    total_active_replicas = 0
    total_non_active_shards = 0
    total_non_active_replicas = 0
    for coll in cluster_state:
        active_shards = sum(
            1 for shard in coll.shards if any(r.state == "active" for r in shard.replicas)
        )
        active_replicas = sum(
            1 for shard in coll.shards for r in shard.replicas if r.state == "active"
        )
        non_active_shards = sum(
            1 for shard in coll.shards if any(r.state != "active" for r in shard.replicas)
        )
        non_active_replicas = sum(
            1 for shard in coll.shards for r in shard.replicas if r.state != "active"
        )
        total_active_shards += active_shards
        total_active_replicas += active_replicas
        total_non_active_shards += non_active_shards
        total_non_active_replicas += non_active_replicas
        table.add_row(coll.name, str(active_shards), str(active_replicas), str(non_active_shards),
                      str(non_active_replicas))

    table.add_row("[bold]TOTAL[/bold]", str(total_active_shards), str(total_active_replicas),
                  str(total_non_active_shards), str(total_non_active_replicas), style="bold")
    rich.print(table)

    if not Confirm.ask("Proceed with removing replicas?"):
        raise typer.Exit(0)

    replicas = validate_num_replicas(replicas)
    tasks = [
        MetaTask(
            [replica.shard.collection.name, replica.shard.name, replica.name],
            asyncio.create_task(send_request(get_overseer_leader(), "/admin/collections",
                                             params={"action": "DELETEREPLICA",
                                                     "collection": replica.shard.collection.name,
                                                     "shard": replica.shard.name, "replica": replica.name})),
        )
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["collection", "shard", "replica"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))


@app.async_command(help=(
    "Add replicas to a collection across selected nodes.\n\n"
    "Examples:\n"
    "  solradm coll populate --collection '^logs-' --shards 1-3 --node 'solr0[12]'\n"
    "  solradm coll populate --collection '^logs-' --exclude-shards 4-6 --node 'solr0[0-4]' --exclude-node 'solr03' --skip-checks --dry"
))
@with_dry_run
@with_cluster_state(CollectionNameFilter, ShardFilter)
async def populate(
        cluster_state: List[Collection],
        node: List[str] | None = typer.Option(
            None,
            "--node",
            help="Regex to select nodes",
            autocompletion=node_names,
        ),
        exclude_node: List[str] | None = typer.Option(
            None,
            "--exclude-node",
            help="Regex to exclude nodes",
            autocompletion=node_names,
        ),
):
    """Populate a single collection with replicas across nodes."""

    if len(cluster_state) != 1:
        rich.print(
            "[error] ❌ More than one collection has been filtered, and this command requires a singular collection!")
        raise typer.Exit(1)

    collection = cluster_state[0]
    shards = collection.shards

    data_nodes = get_nodes_by_role("data").get("on", [])

    selected_nodes = _select_nodes(data_nodes, node, exclude_node)

    if not selected_nodes:
        rich.print("[error] ❌ No nodes match the given selectors")
        raise typer.Exit(1)

    shards_sorted = sorted(shards, key=lambda s: int(re.findall(r"\d+", s.name)[0]))

    num_nodes = len(selected_nodes)
    num_shards = len(shards_sorted)
    base = num_shards // num_nodes
    remainder = num_shards % num_nodes

    node_to_shards = {}
    idx = 0
    for i, n in enumerate(selected_nodes):
        count = base + (1 if i < remainder else 0)
        if count:
            node_to_shards[n] = shards_sorted[idx: idx + count]
            idx += count

    if not node_to_shards:
        rich.print("[warning] ⚠️ No replicas need to be created on the selected nodes")
        raise typer.Exit(0)

    table = Table(title="Planned replicas to add")
    table.add_column("Node")
    table.add_column("Shards")
    for n, shards_list in node_to_shards.items():
        table.add_row(n, ", ".join(s.name for s in shards_list))
    rich.print(table)

    counts = [len(shards_list) for shards_list in node_to_shards.values()]
    avg = sum(counts) / len(counts)
    dist = Counter(counts)

    unused_nodes = sorted(set(selected_nodes) - set(node_to_shards.keys()))

    rich.print(f"Average replicas per node: {avg:.2f}")

    dist_table = Table(title="Replica distribution")
    dist_table.add_column("Replicas")
    dist_table.add_column("Nodes")
    for num, cnt in sorted(dist.items()):
        dist_table.add_row(str(num), str(cnt))
    rich.print(dist_table)

    if unused_nodes:
        rich.print(
            "[warning] ⚠️ The following nodes matched the filter but will not receive replicas: "
            + ", ".join(unused_nodes)
        )

    if not Confirm.ask("Proceed with adding replicas?"):
        raise typer.Exit(0)

    tasks = []
    for n, shards_list in node_to_shards.items():
        for shard in shards_list:
            tasks.append(
                MetaTask(
                    [collection.name, shard.name, n],
                    asyncio.create_task(
                        send_request(
                            get_overseer_leader(),
                            "/admin/collections",
                            params={
                                "action": "ADDREPLICA",
                                "collection": collection.name,
                                "shard": shard.name,
                                "node": n,
                            },
                        )
                    ),
                )
            )

    metatasks = MultiMetaTask(["collection", "shard", "node"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))


@app.async_command(help=(
    "Create a new collection.\n\n"
    "Examples:\n"
    "  solradm coll create search --shards 4 --conf search-config\n"
    "  solradm coll create metrics --shards 6 --upload-conf ./configs/metrics --populate --node 'solr0[1-3]' --dry"
))
@with_dry_run
async def create(
        name: str = typer.Argument(..., help="Name of the collection"),
        shards: int = typer.Option(..., "--shards", help="Number of shards"),
        conf: str = typer.Option(
            None,
            "--conf",
            help="Configuration name in ZooKeeper",
            autocompletion=config_names,
        ),
        upload_conf: Path | None = typer.Option(
            None,
            "--upload-conf",
            exists=False,
            resolve_path=False,
            help="Path or configset name to upload before creation",
        ),
        populate_after: bool = typer.Option(False, "--populate", help="Populate the collection after creation"),
        node: str | None = typer.Option(
            None,
            "--node",
            help="Regex to select nodes for populate",
            autocompletion=node_names,
        ),
):
    """Create a collection in Solr."""

    if upload_conf:
        if conf:
            raise typer.BadParameter("You can't specify both --conf and --upload-conf!")
        from solradm.commands.zk.editor import upload
        upload(paths=[str(upload_conf)], znode_path="/configs", only_used=False, reload=False, exclude=None,
               skip_checks=True)
        conf = os.path.basename(os.path.normpath(upload_conf))
    else:
        if not conf:
            raise typer.BadParameter("Either --conf or --upload-conf must be specified!")

    params = {
        "action": "CREATE",
        "name": name,
        "numShards": shards,
        "collection.configName": conf,
        "createNodeSet": "EMPTY",
    }
    await send_request(get_overseer_leader(), "/admin/collections", params=params)
    rich.print(f"[success] ✅ Created collection {name}!")

    if populate_after:
        await populate(dry_run=api_utils.is_dry_run, collection_name_filter=f"^{name}$", node=[node] if node else None,
                       exclude_node=None)


@app.async_command(help="Delete collections matching a pattern")
@with_dry_run
async def delete(
        pattern: str = typer.Argument(..., help="Regex pattern for collection names"),
):
    """Delete collections and their replicas."""

    fil = CollectionNameFilter(collection_name_filter=pattern)
    cluster_state = fil.apply(get_collections())
    names = [c.name for c in cluster_state]
    if not names:
        rich.print("[error] ❌ No collections match the given pattern")
        raise typer.Exit(1)

    await depopulate(collection_name_filter=pattern, dry_run=api_utils.is_dry_run)
    if api_utils.is_dry_run:
        return

    for name in names:
        await send_request(
            get_overseer_leader(),
            "/admin/collections",
            params={"action": "DELETE", "name": name},
        )
        rich.print(f"[success]✅  Deleted collection {name}!")


@app.async_command(help=(
    "Reload cores for filtered replicas.\n\n"
    "Examples:\n"
    "  solradm coll reload --collection '^logs-' --dry\n"
    "  solradm coll reload --collection '^logs-' --shards 1-2 --replica-type leader --replica-state active\n"
    "  solradm coll reload --collection '^analytics$' --replica-position 1 --exclude-replica-type follower --coordinators true"
))
@with_dry_run
@with_cluster_state(
    CollectionNameFilter,
    ShardFilter,
    ReplicaTypeFilter,
    ReplicaStateFilter,
    ReplicaPositionFilter,
    show_filter_explanations=True,
)
async def reload(
        cluster_state: List[Collection],
        coordinators: bool = typer.Option(None,
                                          help="If unset, reloads both data and coordinator nodes. If set to true, only reload coordinators. If set to false, only reload data nodes.")
):
    """Reload the specified cores and optionally coordinators."""
    replicas: List[Replica] = []
    collection_counts: Counter[str] = Counter()
    selected_collection_names = {collection.name for collection in cluster_state}
    if coordinators is None or not coordinators:
        data_replicas = get_replicas(cluster_state)
        replicas.extend(data_replicas)
        for replica in data_replicas:
            if replica.shard and replica.shard.collection:
                collection_counts[replica.shard.collection.name] += 1
    if coordinators is None or coordinators:
        coordinator_nodes = get_nodes_by_role("coordinator")["on"]
        for node in coordinator_nodes:
            cores = await api_utils.get_cores_from_node(node)
            for core in cores:
                if core.cloud.collection not in selected_collection_names:
                    continue
                replicas.append(
                    Replica(
                        name=core.name,
                        core=core.name,
                        node_name=node,
                        type=core.cloud.replicaType,
                        state=core.lastPublished,
                        leader=True,
                        force_set_state=False,
                        base_url=node,
                    )
                )
                collection_counts[core.cloud.collection] += 1

    replicas = validate_num_replicas(replicas)

    if collection_counts:
        table = Table(title="Planned core reloads", header_style="bold magenta")
        table.add_column("Collection", style="cyan")
        table.add_column("Cores", justify="right", style="green")

        total = 0
        for collection_name in sorted(collection_counts):
            count = collection_counts[collection_name]
            total += count
            table.add_row(collection_name, str(count))

        table.add_row("[bold]TOTAL[/bold]", str(total), style="bold")
        rich.print(table)

        if not Confirm.ask("Proceed with reloading the listed cores?"):
            raise typer.Exit(0)

    tasks = [
        MetaTask(
            [replica.base_url, replica.core],
            asyncio.create_task(send_request(replica.base_url, "/admin/cores",
                                             params={"action": "RELOAD", "core": replica.core})))
        for replica in replicas
    ]
    metatasks = MultiMetaTask(["host", "core"], tasks)
    await metatasks.gather_ignoring_errors(renderer=MultiTaskTable(metatasks, refresh_every=0.25))


@app.async_command(help="Execute a query against a collection")
async def query(
        collection: str = typer.Argument(..., help="Collection to query", autocompletion=collection_names),
        q: str = typer.Argument(..., help="Lucene query string"),
        rows: int = typer.Option(10, help="Number of rows to return"),
        fl: str = typer.Option("*", help="Fields to return"),
        start: int = typer.Option(0, help="Starting offset"),
        fq: List[str] | None = typer.Option(None, "--fq", help="Filter query"),
        param: List[str] | None = typer.Option(
            None,
            "--param",
            "-p",
            help="Additional query parameter in the form name=value"
        ),
        debug: bool = typer.Option(False, help="Include debug information"),
):
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


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _escape_stream_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _get_field_definition(base: str, collection: str, field: str) -> dict:
    session = get_session()
    base_url = get_host_with_scheme(base, "http").rstrip("/")
    for endpoint in ("field", "fields"):
        url = f"{base_url}/solr/{collection}/schema/{endpoint}/{field}"
        async with session.get(url, params={"wt": "json"}) as resp:
            if resp.status == 404:
                continue
            if resp.status != 200:
                body = await resp.text()
                rich.print(
                    f"[error] ❌ Failed to fetch schema metadata for field {field!r}: [yellow]{body}"
                )
                raise typer.Exit(1)
            try:
                data = await resp.json()
            except ContentTypeError:
                body = await resp.text()
                rich.print(
                    f"[error] ❌ Unexpected response while inspecting field {field!r}: [yellow]{body}"
                )
                raise typer.Exit(1)
        if endpoint == "field" and data.get("field"):
            return data["field"]
        if endpoint == "fields" and data.get("fields"):
            fields = data["fields"]
            if fields:
                return fields[0]
    raise typer.BadParameter(
        f"Field {field!r} was not found in the schema for collection {collection!r}"
    )


async def _export_via_export_handler(
    base: str,
    collection: str,
    output: Path,
    query: str,
    fq: List[str] | None,
    fields: List[str],
    requested_fields: List[str],
    unique_key: str,
) -> int:
    session = get_session()
    base_url = get_host_with_scheme(base, "http").rstrip("/")
    url = f"{base_url}/solr/{collection}/export"
    params: dict[str, object] = {
        "q": query,
        "fl": ",".join(fields),
        "sort": f"{unique_key} asc",
        "wt": "json",
    }
    if fq:
        params["fq"] = fq
    data: dict
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            body = await resp.text()
            rich.print(
                f"[error] ❌ Received HTTP {resp.status} from {url}: [yellow]{body}"
            )
            raise typer.Exit(1)
        try:
            data = await resp.json()
        except ContentTypeError:
            body = await resp.text()
            rich.print(
                f"[error] ❌ Unexpected response from {url}: [yellow]{body}"
            )
            raise typer.Exit(1)
    if "error" in data:
        rich.print(
            f"[error] ❌ Export handler returned an error: [yellow]{data['error'].get('msg', data['error'])}"
        )
        raise typer.Exit(1)
    docs = data.get("response", {}).get("docs", [])
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for doc in docs:
            record = {field: doc.get(field) for field in requested_fields}
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")
            count += 1
    return count


async def _export_via_stream_handler(
    base: str,
    collection: str,
    output: Path,
    query: str,
    fq: List[str] | None,
    fields: List[str],
    requested_fields: List[str],
    unique_key: str,
) -> int:
    session = get_session()
    base_url = get_host_with_scheme(base, "http").rstrip("/")
    url = f"{base_url}/solr/{collection}/stream"
    fl_value = ",".join(fields)
    stream_params = [
        f'q="{_escape_stream_value(query)}"',
        f'fl="{_escape_stream_value(fl_value)}"',
        f'sort="{_escape_stream_value(unique_key)} asc"',
        'qt="/select"',
    ]
    if fq:
        for item in fq:
            stream_params.append(f'fq="{_escape_stream_value(item)}"')
    expr = f"search(\"{_escape_stream_value(collection)}\", {', '.join(stream_params)})"
    params = {"expr": expr, "wt": "json"}
    data: dict
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            body = await resp.text()
            rich.print(
                f"[error] ❌ Received HTTP {resp.status} from {url}: [yellow]{body}"
            )
            raise typer.Exit(1)
        try:
            data = await resp.json()
        except ContentTypeError:
            body = await resp.text()
            rich.print(
                f"[error] ❌ Unexpected response from {url}: [yellow]{body}"
            )
            raise typer.Exit(1)
    result_set = data.get("result-set", {})
    docs = result_set.get("docs", [])
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for doc in docs:
            if doc.get("EOF"):
                continue
            record = {field: doc.get(field) for field in requested_fields}
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")
            count += 1
    return count


async def _post_json_docs(
    base: str,
    collection: str,
    docs: List[dict],
    params: dict[str, str],
) -> None:
    if not docs or api_utils.is_dry_run:
        return

    session = get_session()
    base_url = get_host_with_scheme(base, "http").rstrip("/")
    url = f"{base_url}/solr/{collection}/update/json/docs"
    request_params = {"wt": "json", **params}

    async with session.post(url, params=request_params, json=docs) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise SolrException(resp.status, f"HTTP {resp.status}: {body}")
        try:
            data = await resp.json()
        except ContentTypeError:
            body = await resp.text()
            raise SolrException(resp.status, f"Unexpected response: {body}")

    status = data.get("responseHeader", {}).get("status")
    if status != 0:
        message = data.get("error", {}).get("msg", f"Update failed with status {status}")
        raise SolrException(status or 1, message)


async def _send_commit_request(base: str, collection: str, soft_commit: bool) -> None:
    if api_utils.is_dry_run:
        return

    session = get_session()
    base_url = get_host_with_scheme(base, "http").rstrip("/")
    url = f"{base_url}/solr/{collection}/update"
    payload = {"commit": {}}
    if soft_commit:
        payload["commit"]["softCommit"] = True

    async with session.post(url, params={"wt": "json"}, json=payload) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise SolrException(resp.status, f"HTTP {resp.status}: {body}")
        try:
            data = await resp.json()
        except ContentTypeError:
            body = await resp.text()
            raise SolrException(resp.status, f"Unexpected response: {body}")

    status = data.get("responseHeader", {}).get("status")
    if status != 0:
        message = data.get("error", {}).get("msg", f"Commit failed with status {status}")
        raise SolrException(status or 1, message)


@app.async_command(help="Export documents from a collection to a file")
async def export(
        collection: str = typer.Argument(..., help="Collection to export", autocompletion=collection_names),
        output: Path = typer.Argument(..., help="Destination file"),
        field: List[str] = typer.Option(..., "--field", "-f", help="Field to export. Repeat to include multiple fields."),
        fq: List[str] | None = typer.Option(None, "--fq", help="Filter query to apply"),
        query: str = typer.Option("*:*", "--query", "-q", help="Main query to select documents"),
) -> None:
    requested_fields = _dedupe_preserve_order(field)
    if not requested_fields:
        raise typer.BadParameter("At least one --field option must be provided")

    try:
        coordinators = get_nodes_by_role("coordinator").get("on", [])
    except Exception:
        coordinators = []
    base = coordinators[0] if coordinators else get_overseer_leader()

    unique_resp = await send_request(
        base,
        f"/{collection}/schema/uniquekey",
        params={"wt": "json"},
    )
    unique_key = unique_resp.get("uniqueKey")
    if not unique_key:
        rich.print(f"[error] ❌ Unable to determine uniqueKey for collection {collection}")
        raise typer.Exit(1)

    export_fields = _dedupe_preserve_order(requested_fields + [unique_key])
    field_info: dict[str, dict] = {}
    missing_docvalues: set[str] = set()
    multi_valued: set[str] = set()
    non_retrievable: set[str] = set()

    for name in export_fields:
        info = await _get_field_definition(base, collection, name)
        field_info[name] = info
        if name in requested_fields:
            if not info.get("docValues", False):
                missing_docvalues.add(name)
            if info.get("multiValued", False):
                multi_valued.add(name)
            if not info.get("docValues", False) and not info.get("stored", False):
                non_retrievable.add(name)

    if non_retrievable:
        joined = ", ".join(sorted(non_retrievable))
        raise typer.BadParameter(
            f"Field(s) {joined} are neither docValues enabled nor stored; unable to export their values."
        )

    unique_info = field_info.get(unique_key, {})
    export_supported = not missing_docvalues and not multi_valued and unique_info.get("docValues", False)

    if not export_supported:
        reasons: list[str] = []
        if missing_docvalues:
            reasons.append(f"docValues disabled on: {', '.join(sorted(missing_docvalues))}")
        if multi_valued:
            reasons.append(f"multiValued fields: {', '.join(sorted(multi_valued))}")
        if not unique_info.get("docValues", False):
            reasons.append(f"uniqueKey field {unique_key} lacks docValues")
        if reasons:
            rich.print(
                "[warning]⚠️  Requested fields are incompatible with /export (" + "; ".join(reasons) + "). Falling back to /stream."
            )

    try:
        if export_supported:
            count = await _export_via_export_handler(base, collection, output, query, fq, export_fields, requested_fields, unique_key)
            handler = "/export"
        else:
            count = await _export_via_stream_handler(base, collection, output, query, fq, export_fields, requested_fields, unique_key)
            handler = "/stream"
    except SolrException as exc:
        rich.print(f"[error] ❌ Solr returned an error: [yellow]{exc}\n")
        raise typer.Exit(1)

    rich.print(f"[success]✅  Exported {count} documents to {output} using {handler}")


@app.async_command(name="import", help="Import documents from a file into a collection")
@with_dry_run
@with_cluster_state(CollectionNameFilter)
async def import_documents(
        cluster_state: List[Collection],
        source: Path = typer.Argument(
            ...,
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to the JSONL file containing documents to index",
        ),
        batch_size: int = typer.Option(1000, "--batch-size", "-b", help="Number of documents to send per update request"),
        overwrite: bool = typer.Option(True, "--overwrite/--no-overwrite", help="Whether to overwrite documents with the same unique key"),
        commit: bool = typer.Option(True, "--commit/--no-commit", help="Issue a commit after importing the documents"),
        commit_within: int | None = typer.Option(None, "--commit-within", help="Request Solr to commit within the specified milliseconds"),
        soft_commit: bool = typer.Option(False, "--soft-commit", help="Perform a soft commit when issuing the final commit"),
) -> None:
    if batch_size <= 0:
        raise typer.BadParameter("--batch-size must be a positive integer")
    if commit_within is not None and commit_within <= 0:
        raise typer.BadParameter("--commit-within must be a positive integer")
    if soft_commit and not commit:
        rich.print("[warning]⚠️  --soft-commit has no effect when commits are disabled; ignoring.")

    if len(cluster_state) != 1:
        rich.print("[error] ❌ Exactly one collection must match the provided filters")
        raise typer.Exit(1)

    collection = cluster_state[0]

    try:
        coordinators = get_nodes_by_role("coordinator").get("on", [])
    except Exception:
        coordinators = []
    base = coordinators[0] if coordinators else get_overseer_leader()

    base_params: dict[str, str] = {
        "overwrite": "true" if overwrite else "false",
    }
    if commit_within is not None:
        base_params["commitWithin"] = str(commit_within)

    final_params = base_params.copy()
    if commit:
        final_params["commit"] = "true"
        if soft_commit:
            final_params["softCommit"] = "true"

    docs_buffer: List[dict] = []
    total_docs = 0

    async def flush(final: bool = False) -> bool:
        nonlocal docs_buffer, total_docs
        if not docs_buffer:
            return False
        params = final_params if final and commit else base_params
        await _post_json_docs(base, collection.name, docs_buffer, params)
        total_docs += len(docs_buffer)
        docs_buffer = []
        return final and commit

    try:
        with source.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise typer.BadParameter(
                        f"Invalid JSON on line {line_number}: {exc.msg}"
                    ) from exc
                if not isinstance(doc, dict):
                    raise typer.BadParameter(
                        f"Line {line_number} does not contain a JSON object"
                    )
                docs_buffer.append(doc)
                if len(docs_buffer) >= batch_size:
                    await flush()
    except UnicodeDecodeError as exc:
        raise typer.BadParameter(f"Failed to decode {source}: {exc}") from exc
    except OSError as exc:
        raise typer.BadParameter(f"Failed to read {source}: {exc}") from exc

    committed_with_docs = await flush(final=True)

    if commit and not committed_with_docs and total_docs > 0:
        await _send_commit_request(base, collection.name, soft_commit)

    handler = "/update/json/docs"
    if api_utils.is_dry_run:
        rich.print(
            f"[success]✅  Dry run: {total_docs} documents would be imported into {collection.name} using {handler}"
        )
    else:
        rich.print(
            f"[success]✅  Imported {total_docs} documents into {collection.name} using {handler}"
        )


def _parse_status(json_resp):
    msgs = json_resp.get("statusMessages", {})
    percent = None
    processed = None
    total = None
    for k, v in msgs.items():
        match = re.search(r"(\d+)", str(v))
        if not match:
            continue
        num = int(match.group(1))
        lk = k.lower()
        if "percent" in lk:
            percent = num
        elif "processed" in lk:
            processed = num
        elif "total" in lk:
            total = num
    if percent is not None:
        return percent, 100, json_resp.get("status")
    return processed or 0, total, json_resp.get("status")


def _get_collection_from_context(context_zk: str, collection: str) -> Collection:
    zk = KazooClient(hosts=context_zk, timeout=5)
    zk.start()
    try:
        data, _ = zk.get(f"/collections/{collection}/state.json")
    finally:
        zk.stop()
        zk.close()
    state = json.loads(data.decode("utf-8"))[collection]
    state["name"] = collection
    return Collection.model_validate(state)


@app.async_command(
    help="Reindex documents from a source collection into a target collection using the dataimport handler")
async def reindex(
        source_collection: str = typer.Option(
            ..., "--source", help="Collection to reindex from", autocompletion=source_collection_names
        ),
        target_collection: str = typer.Option(..., "--target", help="Collection to reindex into",
                                              autocompletion=collection_names),
        source_context: str | None = typer.Option(None, "--source-context", help="Context of the source collection",
                                                  autocompletion=context_names),
        handler: str = typer.Option("/dataimport", "--handler", help="Path of the dataimport handler"),
        fq: List[str] | None = typer.Option(None, "--fq", help="Filter query to pass to the dataimport handler"),
        source_shard: List[str] | None = typer.Option(None, "--source-shard", help="Source shards to reindex"),
):
    current_ctx = get_current_context()
    cluster_state = get_collections()
    target_coll = next((c for c in cluster_state if c.name == target_collection), None)
    if not target_coll:
        rich.print(f"[error]❌  Target collection {target_collection} not found")
        raise typer.Exit(1)

    if source_context:
        ctx = next((c for c in settings.contexts.available if c.name == source_context), None)
        if not ctx:
            rich.print(f"[error]❌  Source context {source_context} not found")
            raise typer.Exit(1)
        source_coll = _get_collection_from_context(ctx.zk, source_collection)
        source_zk = ctx.zk
    else:
        source_coll = next((c for c in cluster_state if c.name == source_collection), None)
        if not source_coll:
            rich.print(f"[error]❌  Source collection {source_collection} not found")
            raise typer.Exit(1)
        source_zk = current_ctx.zk

    src_shards = [s for s in source_coll.shards if not source_shard or s.name in source_shard]
    if not src_shards:
        rich.print("[error]❌  No source shards matched")
        raise typer.Exit(1)

    tgt_shards = sorted(target_coll.shards, key=lambda s: s.name)
    src_shards_sorted = sorted(src_shards, key=lambda s: s.name)

    shard_map: dict[str, List[Shard]] = {}
    if len(tgt_shards) >= len(src_shards_sorted):
        for idx, src in enumerate(src_shards_sorted):
            shard_map.setdefault(tgt_shards[idx].name, []).append(src)
    else:
        for idx, src in enumerate(src_shards_sorted):
            shard_map.setdefault(tgt_shards[idx % len(tgt_shards)].name, []).append(src)

    leaders = {shard.name: next((r for r in shard.replicas if r.leader), None) for shard in target_coll.shards}
    busy = []
    for name, rep in leaders.items():
        if rep is None:
            continue
        status = await send_request(rep.base_url, f"/{target_collection}{handler}",
                                    params={"command": "status", "wt": "json"})
        if status.get("status") == "busy":
            busy.append((name, rep))

    if busy:
        rich.print("[warning]⚠️  Dataimport already running on some shards. Monitoring progress...")
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                      TimeRemainingColumn()) as progress:
            tasks = {name: progress.add_task(name, total=100) for name, _ in busy}

            async def monitor(replica: Replica, name: str):
                while True:
                    stat = await send_request(replica.base_url, f"/{target_collection}{handler}",
                                              params={"command": "status", "wt": "json"})
                    done, total, st = _parse_status(stat)
                    if total:
                        progress.update(tasks[name], total=total, completed=done)
                    else:
                        progress.update(tasks[name], completed=done)
                    if st != "busy":
                        break
                    await asyncio.sleep(1)

            await asyncio.gather(*(monitor(rep, name) for name, rep in busy))
        raise typer.Exit(1)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), TimeRemainingColumn()) as progress:

        async def run_target(shard_name: str, src_list: List[Shard]):
            leader = leaders[shard_name]
            task_id = progress.add_task(shard_name, total=100)
            for src in src_list:
                params = {
                    "command": "full-import",
                    "clean": "false",
                    "commit": "true",
                    "distrib": "false",
                    "wt": "json",
                    "sourceZkHost": source_zk,
                    "sourceCollection": source_collection,
                    "sourceShard": src.name,
                }
                if fq:
                    params["fq"] = fq
                await send_request(leader.base_url, f"/{target_collection}{handler}", params=params)

                while True:
                    stat = await send_request(leader.base_url, f"/{target_collection}{handler}",
                                              params={"command": "status", "wt": "json"})
                    done, total, st = _parse_status(stat)
                    if total:
                        progress.update(task_id, total=total, completed=done)
                    else:
                        progress.update(task_id, completed=done)
                    if st != "busy":
                        break
                    await asyncio.sleep(1)
            progress.update(task_id, completed=progress.tasks[task_id].total or progress.tasks[task_id].completed)

        await asyncio.gather(*(run_target(name, shards) for name, shards in shard_map.items()))

    rich.print("[success]✅  Reindex completed")