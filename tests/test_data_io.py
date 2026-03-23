import asyncio

import pytest
import typer

from solradm.commands.collections.data_io import (
    _get_field_definition,
    _parse_luke_schema_flags,
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
