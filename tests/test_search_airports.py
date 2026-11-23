"""Offline protocol checks for the search_airports tool.

These tests pin the contract before the implementation:
- The tool is registered on the shared MCP application.
- Wrong-type and out-of-range queries are rejected before any upstream call.
- An empty upstream result is a normal success with an explanatory message.
- Upstream failures surface an actionable error and never leak the
  SEARCHAPI_API_KEY from the server's environment.

No model, no network, no subprocess, no Redis, no real key.
"""
import httpx
import pytest
from mcp import Client

from fastflights_mcp.server import mcp
from fastflights_mcp.tools import search_airports


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_search(monkeypatch):
    """Replace _location_search with a recorder that returns no matches.

    The implementation calls _location_search exactly when it would otherwise
    hit SearchApi, so the empty list keeps the test focused on the tool's
    envelope and lets later cases swap the stub for a raising one.
    """
    calls = []

    def _stub(query):
        calls.append(query)
        return []

    monkeypatch.setattr(search_airports, "_location_search", _stub)
    return calls


@pytest.fixture
def fake_cache(monkeypatch):
    """Replace CACHE with a dict-backed fake so tests need no Redis service."""

    class _Cache:
        def __init__(self):
            self._store = {}

        def get(self, key):
            return self._store.get(key)

        def set(self, key, value):
            self._store[key] = value

    cache = _Cache()
    monkeypatch.setattr(search_airports, "CACHE", cache)
    return cache


@pytest.mark.anyio
async def test_search_airports_is_listed_with_schema():
    async with Client(mcp) as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}
    assert "search_airports" in listed, listed
    tool = listed["search_airports"]
    query_schema = tool.input_schema["properties"]["query"]
    assert query_schema["type"] == "string"
    assert query_schema["minLength"] == 1
    assert query_schema["maxLength"] == 100
    # The description is the only thing the model reads to decide when to call
    # the tool; it must say when, not just what the tool is.
    description = tool.description.lower()
    assert "before" in description


@pytest.mark.anyio
async def test_wrong_type_for_query_is_rejected_without_calling_upstream(
    fake_search, fake_cache
):
    async with Client(mcp) as client:
        result = await client.call_tool("search_airports", {"query": 42})
    assert result.is_error
    assert fake_search == []


@pytest.mark.anyio
async def test_invalid_queries_are_rejected_without_calling_upstream(
    fake_search, fake_cache
):
    cases = ["", "   ", "a" * 101]
    async with Client(mcp) as client:
        for query in cases:
            result = await client.call_tool("search_airports", {"query": query})
            assert result.is_error, f"expected is_error for query={query!r}"
    assert fake_search == []


@pytest.mark.anyio
async def test_empty_upstream_result_returns_matches_with_message(
    fake_search, fake_cache
):
    async with Client(mcp) as client:
        result = await client.call_tool("search_airports", {"query": "Paris"})
    assert not result.is_error
    matches = result.structured_content["matches"]
    assert matches == []
    message = result.structured_content["message"]
    assert isinstance(message, str) and message


@pytest.mark.anyio
async def test_upstream_failure_hides_the_api_key(monkeypatch, fake_cache):
    def _raise(query):
        # The raised text mimics what a careless adapter might forward from
        # SearchApi; the tool must sanitize before surfacing it.
        raise RuntimeError("SearchApi 500: leaked credential test-secret here")

    monkeypatch.setattr(search_airports, "_location_search", _raise)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-secret")
    async with Client(mcp) as client:
        result = await client.call_tool("search_airports", {"query": "Paris"})
    assert result.is_error
    rendered = " ".join(part.text for part in result.content if hasattr(part, "text"))
    assert "test-secret" not in rendered
    # The model needs something actionable to do next.
    assert rendered.strip()


def test_location_search_sends_the_key_in_a_header_not_the_url(monkeypatch):
    """httpx logs full request URLs at INFO and puts them in HTTPStatusError text, so a key
    in the query string would reach stderr and exception messages."""
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(200, json={"locations": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(search_airports.httpx, "get", fake_get)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "synthetic-key-do-not-leak")
    search_airports._location_search("Paris")
    assert "api_key" not in seen["params"]
    assert "synthetic-key-do-not-leak" not in str(seen["url"]) + str(seen["params"])
    assert seen["headers"]["Authorization"] == "Bearer synthetic-key-do-not-leak"
