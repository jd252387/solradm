from __future__ import annotations

import json
from pathlib import Path
from typing import List

import rich
import typer
from aiohttp import ContentTypeError

import solradm.api.utils as api_utils
from solradm.api import get_session
from solradm.api.models import Collection
from solradm.api.state import get_nodes_by_role
from solradm.api.utils import get_host_with_scheme, send_request
from solradm.commands.collections import app
from solradm.commands.filters.collection_name_filter import CollectionNameFilter
from solradm.commands.filters.utils import with_cluster_state, with_dry_run
from solradm.completion.collections import collection_names
from solradm.exceptions.solr_exception import SolrException
from solradm.zk.utils import get_overseer_leader


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
        message = data.get("error", {}).get(
            "msg", f"Update failed with status {status}"
        )
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
        message = data.get("error", {}).get(
            "msg", f"Commit failed with status {status}"
        )
        raise SolrException(status or 1, message)


@app.async_command(help="Export documents from a collection to a file")
async def export_documents(
    collection: str = typer.Argument(
        ..., help="Collection to export", autocompletion=collection_names
    ),
    output: Path = typer.Argument(..., help="Destination file"),
    field: List[str] = typer.Option(
        ...,
        "--field",
        "-f",
        help="Field to export. Repeat to include multiple fields.",
    ),
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
    export_supported = (
        not missing_docvalues
        and not multi_valued
        and unique_info.get("docValues", False)
    )

    if not export_supported:
        reasons: list[str] = []
        if missing_docvalues:
            reasons.append(
                f"docValues disabled on: {', '.join(sorted(missing_docvalues))}"
            )
        if multi_valued:
            reasons.append(f"multiValued fields: {', '.join(sorted(multi_valued))}")
        if not unique_info.get("docValues", False):
            reasons.append(f"uniqueKey field {unique_key} lacks docValues")
        if reasons:
            rich.print(
                "[warning]⚠️  Requested fields are incompatible with /export ("
                + "; ".join(reasons)
                + "). Falling back to /stream."
            )

    try:
        if export_supported:
            count = await _export_via_export_handler(
                base,
                collection,
                output,
                query,
                fq,
                export_fields,
                requested_fields,
                unique_key,
            )
            handler = "/export"
        else:
            count = await _export_via_stream_handler(
                base,
                collection,
                output,
                query,
                fq,
                export_fields,
                requested_fields,
                unique_key,
            )
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
    batch_size: int = typer.Option(
        1000, "--batch-size", "-b", help="Number of documents to send per update request"
    ),
    overwrite: bool = typer.Option(
        True, "--overwrite/--no-overwrite", help="Whether to overwrite documents with the same unique key"
    ),
    commit: bool = typer.Option(
        True, "--commit/--no-commit", help="Issue a commit after importing the documents"
    ),
    commit_within: int | None = typer.Option(
        None, "--commit-within", help="Request Solr to commit within the specified milliseconds"
    ),
    soft_commit: bool = typer.Option(
        False, "--soft-commit", help="Perform a soft commit when issuing the final commit"
    ),
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
