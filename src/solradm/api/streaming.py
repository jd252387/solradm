"""HTTP streaming support for Solr export operations."""
from __future__ import annotations

import json
from typing import AsyncIterator
from urllib.parse import urljoin

import aiohttp

from solradm.api import get_session
from solradm.api.utils import get_host_with_scheme


class StreamingError(Exception):
    """Raised when streaming encounters an error."""

    def __init__(self, message: str):
        super().__init__(message)


async def stream_json_docs(
    host: str,
    endpoint: str,
    params: dict | None = None
    ) -> AsyncIterator[dict]:
    """
    Stream JSON documents from a Solr endpoint.

    Yields individual document dictionaries as they arrive over HTTP,
    without buffering the entire response in memory.

    Args:
        host: The Solr host URL
        endpoint: The API endpoint (e.g., '/collection/export')
        params: Query parameters
        config: StreamConfig defining the response structure

    Yields:
        Individual document dictionaries

    Raises:
        StreamingError: On HTTP errors or malformed JSON
    """
    url = urljoin(get_host_with_scheme(host, "http"), "/solr" + endpoint)
    session = get_session()

    try:
        async with session.get(url, params=params) as resp:
            if not resp.ok:
                text = await resp.text()
                raise StreamingError(text[:500])

            async for doc in _parse_streaming_response(resp):
                yield doc
    except aiohttp.ClientError as e:
        raise StreamingError(f"Connection error: {e}")


async def _parse_streaming_response(
    resp: aiohttp.ClientResponse,
) -> AsyncIterator[dict]:
    """
    Parse a streaming JSON response, yielding documents.

    Expects Solr's response format where each document is on its own line
    within a JSON array structure like:
        {"result-set":{"docs":[{"field1":"xxx"},{"field2":"xxx"},{"field3":"xxx"},{"EOF":true,"RESPONSE_TIME":33}]}}
    """
    buffer = ""
    in_docs_array = False
    brace_count = 0
    object_start = -1
    in_string = False
    escape_next = False

    async for chunk in resp.content.iter_any():
        buffer += chunk.decode("utf-8")

        # Find the start of docs array if we haven't yet
        if not in_docs_array:
            docs_marker = '"docs":['
            docs_pos = buffer.find(docs_marker)
            if docs_pos != -1:
                in_docs_array = True
                buffer = buffer[docs_pos + len(docs_marker) :]
            else:
                # Keep tail in case marker spans chunks
                if len(buffer) > len(docs_marker):
                    buffer = buffer[-len(docs_marker) :]
                continue

        # Parse objects from the buffer character by character
        i = 0
        while i < len(buffer):
            char = buffer[i]

            if escape_next:
                escape_next = False
                i += 1
                continue

            if char == "\\" and in_string:
                escape_next = True
                i += 1
                continue

            if char == '"':
                in_string = not in_string
            elif not in_string:
                if char == "{":
                    if brace_count == 0:
                        object_start = i
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0 and object_start >= 0:
                        # Complete object found
                        obj_str = buffer[object_start : i + 1]
                        try:
                            doc = json.loads(obj_str)
                        except json.JSONDecodeError as e:
                            raise StreamingError(f"Invalid JSON in stream: {e}")

                        if "EOF" in doc:
                            if "EXCEPTION" in doc:
                                raise StreamingError(doc['EXCEPTION'])
                            return  # End of stream

                        yield doc

                        # Trim buffer and reset position
                        buffer = buffer[i + 1 :]
                        i = -1
                        object_start = -1

            i += 1

        # Keep unparsed portion (incomplete object) in buffer
        if object_start > 0:
            buffer = buffer[object_start:]
            object_start = 0
