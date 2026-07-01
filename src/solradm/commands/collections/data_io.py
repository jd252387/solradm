from __future__ import annotations

import json
from pathlib import Path
from typing import List

import rich
import typer
from rich.prompt import Confirm

import solradm.api.utils as api_utils
from solradm.api.models import Collection
from solradm.api.state import get_collections, get_nodes_by_role
from solradm.api.utils import send_request
from solradm.commands.collections.subapp import app
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


_SCHEMA_FLAG_MAP = {
    "I": "indexed",
    "T": "tokenized",
    "S": "stored",
    "D": "docValues",
    "U": "uninvertible",
    "M": "multiValued",
    "V": "termVectors",
    "o": "termVectorOffsets",
    "p": "termVectorPositions",
    "y": "termVectorPayloads",
    "O": "omitNorms",
    "F": "omitTermFreqAndPositions",
    "P": "omitPositions",
    "H": "storeOffsetsWithPositions",
    "L": "lazy",
    "B": "binary",
    "f": "sortMissingFirst",
    "l": "sortMissingLast",
}


def _parse_luke_schema_flags(flags: str | None) -> dict[str, bool]:
    parsed = {setting: False for setting in _SCHEMA_FLAG_MAP.values()}
    if not flags:
        return parsed

    for flag in flags:
        setting = _SCHEMA_FLAG_MAP.get(flag)
        if setting:
            parsed[setting] = True
    return parsed


async def _get_field_definition(base: str, collection: str, field: str) -> dict:
    resp = await send_request(
        base,
        f"/{collection}/admin/luke",
        params={"wt": "json"},
        return_raw=True,
    )
    if resp["status"] == 404:
        raise typer.BadParameter(
            f"Field {field!r} was not found in the schema for collection {collection!r}"
        )
    if not resp["ok"]:
        error_text = resp["error_text"] or str(resp["data"])
        rich.print(
            f"[error] ❌ Failed to fetch schema metadata for field {field!r}: [yellow]{error_text}"
        )
        raise typer.Exit(1)
    if resp["data"] is None:
        rich.print(
            f"[error] ❌ Unexpected response while inspecting field {field!r}: [yellow]{resp['error_text']}"
        )
        raise typer.Exit(1)
    data = resp["data"]
    schema = data.get("schema", {})
    fields = schema.get("fields", {})
    field_info = fields.get(field)
    if field_info:
        return {
            **field_info,
            **_parse_luke_schema_flags(field_info.get("flags")),
        }
    raise typer.BadParameter(
        f"Field {field!r} was not found in the schema for collection {collection!r}"
    )


def _select_collection_luke_base(collections: List[Collection], collection_name: str) -> str:
    collection = next(
        (candidate for candidate in collections if candidate.name == collection_name),
        None,
    )
    if collection is None:
        raise typer.BadParameter(f"Collection {collection_name!r} was not found")

    try:
        data_nodes = set(get_nodes_by_role("data").get("on", []))
    except Exception:
        data_nodes = set()

    replicas = [
        replica
        for shard in collection.shards
        for replica in shard.replicas
        if replica.base_url and replica.core
    ]
    if not replicas:
        raise typer.BadParameter(
            f"Collection {collection_name!r} has no replica with a usable Solr base URL"
        )

    def replica_priority(replica) -> tuple[bool, bool, bool]:
        is_active = replica.state.lower() == "active"
        is_data_node = (
            not data_nodes
            or replica.node_name in data_nodes
            or replica.base_url in data_nodes
        )
        return is_active and is_data_node, is_active, is_data_node

    selected = max(replicas, key=replica_priority)
    return selected.base_url


def _build_stream_expr_params(
    collection: str,
    query: str,
    fq: List[str] | None,
    fields: List[str],
    sort_field: str,
    qt: str = "/vanilla",
    rows: int | None = None,
) -> dict[str, str]:
    """Build parameters for the /stream handler using streaming expressions."""
    fl_value = ",".join(fields)
    stream_params = [
        f'q="{_escape_stream_value(query)}"',
        f'fl="{_escape_stream_value(fl_value)}"',
        f'sort="{_escape_stream_value(sort_field)}"',
        f'qt="{qt}"',
    ]
    if fq:
        for item in fq:
            stream_params.append(f'fq="{_escape_stream_value(item)}"')
    if qt != "/export" and rows is not None:
        stream_params.append(f'rows="{rows}"')
    expr = f'search("{_escape_stream_value(collection)}", {", ".join(stream_params)})'
    return {"expr": expr, "wt": "json"}


async def _stream_export_docs(
    base: str,
    collection: str,
    output: Path,
    query: str,
    fq: List[str] | None,
    fields: List[str],
    requested_fields: List[str],
    sort_field: str,
    qt: str,
    rows: int | None,
) -> int:
    """
    Export documents using HTTP streaming via the /stream endpoint.

    Uses qt=/export when use_export_handler is True (requires docValues, no multiValued),
    otherwise uses qt=/vanilla.
    """
    from solradm.api.streaming import (
        StreamingError,
        stream_json_docs,
    )

    endpoint = f"/{collection}/stream"
    params = _build_stream_expr_params(collection, query, fq, fields, sort_field, qt, rows)

    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    try:
        with output.open("w", encoding="utf-8") as fh:
            async for doc in stream_json_docs(base, endpoint, params):
                record = {field: doc.get(field) for field in requested_fields}
                fh.write(json.dumps(record, ensure_ascii=False))
                fh.write("\n")
                count += 1
    except StreamingError as e:
        rich.print(f"[error] ❌ Export failed: [yellow]{e}")
        raise typer.Exit(1)

    return count


async def _post_json_docs(
    base: str,
    collection: str,
    docs: List[dict],
    params: dict[str, str],
) -> None:
    if not docs or api_utils.is_dry_run:
        return

    request_params = {"wt": "json", **params}
    resp = await send_request(
        base,
        f"/{collection}/update/json/docs",
        params=request_params,
        method="POST",
        json_body=docs,
        return_raw=True,
    )
    if not resp["ok"]:
        error_text = resp["error_text"] or str(resp["data"])
        raise SolrException(resp["status"], f"HTTP {resp['status']}: {error_text}")
    if resp["data"] is None:
        raise SolrException(resp["status"], f"Unexpected response: {resp['error_text']}")

    data = resp["data"]
    status = data.get("responseHeader", {}).get("status")
    if status != 0:
        message = data.get("error", {}).get(
            "msg", f"Update failed with status {status}"
        )
        raise SolrException(status or 1, message)


async def _send_commit_request(base: str, collection: str, soft_commit: bool) -> None:
    if api_utils.is_dry_run:
        return

    payload = {"commit": {}}
    if soft_commit:
        payload["commit"]["softCommit"] = True

    resp = await send_request(
        base,
        f"/{collection}/update",
        params={"wt": "json"},
        method="POST",
        json_body=payload,
        return_raw=True,
    )
    if not resp["ok"]:
        error_text = resp["error_text"] or str(resp["data"])
        raise SolrException(resp["status"], f"HTTP {resp['status']}: {error_text}")
    if resp["data"] is None:
        raise SolrException(resp["status"], f"Unexpected response: {resp['error_text']}")

    data = resp["data"]
    status = data.get("responseHeader", {}).get("status")
    if status != 0:
        message = data.get("error", {}).get(
            "msg", f"Commit failed with status {status}"
        )
        raise SolrException(status or 1, message)


@app.command(help="Export documents from a collection to a file")
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
    sort: str | None = typer.Option(
        None,
        "--sort",
        "-s",
        help="Sort order for export (e.g., 'field1 asc, field2 desc'). Defaults to first field ascending.",
    ),
    rows: int = typer.Option(
        1000,
        "--rows",
        help="Number of rows to request per stream page when using a non-/export handler.",
    ),
    qt: str | None = typer.Option(
        None,
        "--qt",
        help="Override the request handler used in the streaming expression (for example /export or /select).",
    ),
) -> None:
    if rows <= 0:
        raise typer.BadParameter("--rows must be a positive integer")
    if qt is not None and not qt.strip():
        raise typer.BadParameter("--qt must not be empty")

    requested_fields = _dedupe_preserve_order(field)
    if not requested_fields:
        raise typer.BadParameter("At least one --field option must be provided")

    try:
        coordinators = get_nodes_by_role("coordinator").get("on", [])
    except Exception:
        coordinators = []
    base = coordinators[0] if coordinators else get_overseer_leader()
    luke_base = _select_collection_luke_base(get_collections(), collection)

    export_fields = requested_fields
    missing_docvalues: set[str] = set()
    multi_valued: set[str] = set()
    non_retrievable: set[str] = set()

    for name in export_fields:
        info = await _get_field_definition(luke_base, collection, name)
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

    export_supported = not missing_docvalues and not multi_valued

    reasons: list[str] = []
    if missing_docvalues:
        reasons.append(
            f"docValues disabled on: {', '.join(sorted(missing_docvalues))}"
        )
    if multi_valued:
        reasons.append(f"multiValued fields: {', '.join(sorted(multi_valued))}")

    sort_field = sort if sort else f"{requested_fields[0]} asc"
    selected_qt = qt if qt is not None else ("/export" if export_supported else "/vanilla")

    if qt is None and not export_supported and reasons:
        rich.print(
            "[warning]⚠️  Requested fields are incompatible with /export ("
            + "; ".join(reasons)
            + "). Falling back to /vanilla."
        )
    elif selected_qt == "/export" and not export_supported and reasons:
        rich.print(
            "[warning]⚠️  Requested fields are incompatible with /export ("
            + "; ".join(reasons)
            + "). The export may fail because /export was requested explicitly."
        )

    if selected_qt == "/export":
        rich.print(
            "[warning]⚠️  Using /export always exports all documents matching the provided filter queries; --rows is ignored."
        )
        if not Confirm.ask("Proceed with /export and export every matching document?"):
            rich.print("[warning]⚠️  Export cancelled.")
            raise typer.Exit(1)

    count = await _stream_export_docs(
        base,
        collection,
        output,
        query,
        fq,
        export_fields,
        requested_fields,
        sort_field,
        selected_qt,
        rows,
    )

    rich.print(f"[success]✅  Exported {count} documents to {output} using {selected_qt}")


@app.command(name="import", help="Import documents from a file into a collection")
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
