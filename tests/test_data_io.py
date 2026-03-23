import asyncio

import pytest
import typer

from solradm.commands.collections.data_io import (
    _build_stream_expr_params,
    _get_field_definition,
    _parse_luke_schema_flags,
    export_documents,
)


def test_parse_luke_schema_flags_extracts_docvalues_and_multivalued():
    parsed = _parse_luke_schema_flags("ITSD-M------------")

    assert parsed["indexed"] is True
    assert parsed["tokenized"] is True
    assert parsed["stored"] is True
    assert parsed["docValues"] is True
    assert parsed["multiValued"] is True
    assert parsed["sortMissingLast"] is False


def test_parse_luke_schema_flags_handles_empty_values():
    parsed = _parse_luke_schema_flags(None)

    assert parsed["docValues"] is False
    assert parsed["stored"] is False


def test_get_field_definition_reads_schema_fields_from_luke(monkeypatch):
    async def fake_send_request(base, path, params, return_raw):
        assert path == "/books/admin/luke"
        assert params == {"wt": "json"}
        assert return_raw is True
        return {
            "status": 200,
            "ok": True,
            "error_text": None,
            "data": {
                "schema": {
                    "fields": {
                        "id": {
                            "type": "string",
                            "flags": "I-SD-------------",
                        }
                    }
                }
            },
        }

    monkeypatch.setattr(
        "solradm.commands.collections.data_io.send_request",
        fake_send_request,
    )

    info = asyncio.run(_get_field_definition("http://solr", "books", "id"))

    assert info["type"] == "string"
    assert info["indexed"] is True
    assert info["stored"] is True
    assert info["docValues"] is True
    assert info["multiValued"] is False


def test_get_field_definition_raises_when_field_missing(monkeypatch):
    async def fake_send_request(base, path, params, return_raw):
        return {
            "status": 200,
            "ok": True,
            "error_text": None,
            "data": {"schema": {"fields": {}}},
        }

    monkeypatch.setattr(
        "solradm.commands.collections.data_io.send_request",
        fake_send_request,
    )

    with pytest.raises(typer.BadParameter):
        asyncio.run(_get_field_definition("http://solr", "books", "missing"))


def test_build_stream_expr_params_includes_rows_for_non_export_handler():
    params = _build_stream_expr_params(
        "books",
        "*:*",
        ["type:book"],
        ["id", "title"],
        "id asc",
        qt="/vanilla",
        rows=500,
    )

    assert 'rows="500"' in params["expr"]
    assert 'qt="/vanilla"' in params["expr"]


def test_build_stream_expr_params_omits_rows_for_export_handler():
    params = _build_stream_expr_params(
        "books",
        "*:*",
        ["type:book"],
        ["id", "title"],
        "id asc",
        qt="/export",
        rows=500,
    )

    assert 'qt="/export"' in params["expr"]
    assert 'rows="500"' not in params["expr"]


def test_export_documents_prompts_before_using_export(monkeypatch, tmp_path):
    prompts: list[str] = []

    monkeypatch.setattr(
        "solradm.commands.collections.data_io.get_nodes_by_role",
        lambda _role: {"on": ["http://coordinator"]},
    )

    async def fake_get_field_definition(base, collection, field):
        return {"docValues": True, "stored": True, "multiValued": False}

    async def fake_stream_export_docs(*args, **kwargs):
        raise AssertionError("stream export should not be called when prompt is declined")

    monkeypatch.setattr(
        "solradm.commands.collections.data_io._get_field_definition",
        fake_get_field_definition,
    )
    monkeypatch.setattr(
        "solradm.commands.collections.data_io._stream_export_docs",
        fake_stream_export_docs,
    )
    monkeypatch.setattr(
        "solradm.commands.collections.data_io.Confirm.ask",
        lambda prompt: prompts.append(prompt) or False,
    )

    with pytest.raises(typer.Exit) as exc_info:
        asyncio.run(
            export_documents(
                collection="books",
                output=tmp_path / "out.jsonl",
                field=["id"],
                fq=["type:book"],
                query="*:*",
                sort=None,
                rows=1000,
                qt=None,
            )
        )

    assert exc_info.value.exit_code == 1
    assert prompts == ["Proceed with /export and export every matching document?"]


def test_export_documents_passes_rows_for_vanilla_handler(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(
        "solradm.commands.collections.data_io.get_nodes_by_role",
        lambda _role: {"on": ["http://coordinator"]},
    )

    async def fake_get_field_definition(base, collection, field):
        return {"docValues": False, "stored": True, "multiValued": False}

    async def fake_stream_export_docs(base, collection, output, query, fq, fields, requested_fields, sort_field, qt, rows):
        captured.update({
            "base": base,
            "collection": collection,
            "output": output,
            "query": query,
            "fq": fq,
            "fields": fields,
            "requested_fields": requested_fields,
            "sort_field": sort_field,
            "qt": qt,
            "rows": rows,
        })
        return 3

    monkeypatch.setattr(
        "solradm.commands.collections.data_io._get_field_definition",
        fake_get_field_definition,
    )
    monkeypatch.setattr(
        "solradm.commands.collections.data_io._stream_export_docs",
        fake_stream_export_docs,
    )

    asyncio.run(
        export_documents(
            collection="books",
            output=tmp_path / "out.jsonl",
            field=["id"],
            fq=["type:book"],
            query="*:*",
            sort=None,
            rows=250,
            qt=None,
        )
    )

    assert captured["qt"] == "/vanilla"
    assert captured["rows"] == 250


def test_export_documents_uses_qt_override(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(
        "solradm.commands.collections.data_io.get_nodes_by_role",
        lambda _role: {"on": ["http://coordinator"]},
    )

    async def fake_get_field_definition(base, collection, field):
        return {"docValues": True, "stored": True, "multiValued": False}

    async def fake_stream_export_docs(
        base,
        collection,
        output,
        query,
        fq,
        fields,
        requested_fields,
        sort_field,
        qt,
        rows,
    ):
        captured.update({"qt": qt, "rows": rows})
        return 1

    monkeypatch.setattr(
        "solradm.commands.collections.data_io._get_field_definition",
        fake_get_field_definition,
    )
    monkeypatch.setattr(
        "solradm.commands.collections.data_io._stream_export_docs",
        fake_stream_export_docs,
    )

    asyncio.run(
        export_documents(
            collection="books",
            output=tmp_path / "out.jsonl",
            field=["id"],
            fq=["type:book"],
            query="*:*",
            sort=None,
            rows=75,
            qt="/select",
        )
    )

    assert captured == {"qt": "/select", "rows": 75}
