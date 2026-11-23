"""Offline protocol checks for the search_airports tool.

These tests pin the contract before the implementation:
- The tool is registered on the shared MCP application.
- Wrong-type and out-of-range queries are rejected before any upstream call.
- An empty upstream result is a normal success with an explanatory message.
- Upstream failures surface an actionable error and never leak the
  SEARCHAPI_API_KEY from the server's environment.

No model, no network, no subprocess, no Redis, no real key.
"""
import json
from pathlib import Path

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


def test_location_search_sends_the_text_as_q(monkeypatch):
    """Recorded live on 2026-11-23: SearchApi's Location Search answers HTTP 400
    "Missing required parameter q." when the text is sent as `query`."""
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(200, json={"locations": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(search_airports.httpx, "get", fake_get)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "synthetic-key-do-not-leak")
    search_airports._location_search("New York")
    assert seen["params"] == {"engine": "google_flights_location_search", "q": "New York"}


# --- Mapping the real Location Search response ----------------------------------------------

LOCATION_FIXTURE = Path(__file__).parent / "fixtures" / "searchapi" / "location_search_new_york.json"


def new_york_locations():
    return json.loads(LOCATION_FIXTURE.read_text())["locations"]


def place(name, *codes):
    """Synthetic place in the recorded shape: a name and a nested list of airports."""
    return {"full_name": name, "type": "city", "airports": [
        {"type": "airport", "airport_code": code, "title": f"{code} Airport", "city": name}
        for code in codes]}


def test_matches_are_the_airports_of_the_best_matching_place():
    """Recorded: 'New York' returns 5 places. Only the first (the city) is the match; the
    other four are upstate cities (Buffalo, Niagara Falls, Rochester, Albany)."""
    locations = new_york_locations()
    matches, place_name = search_airports._to_matches(locations)
    assert place_name == "New York"
    first = locations[0]["airports"]
    assert [m.iata for m in matches] == [a["airport_code"] for a in first] == ["JFK", "LGA", "EWR"]
    assert [m.name for m in matches] == [a["title"] for a in first]
    assert [m.city for m in matches] == [a["city"] for a in first]
    assert [m.country for m in matches] == ["US", "US", "US"]  # from airportsdata; SearchApi has none


def test_duplicates_and_invalid_codes_are_skipped():
    locations = [place("X", "JFK", "JFK", "12", "LGA")]
    matches, _ = search_airports._to_matches(locations)
    assert [m.iata for m in matches] == ["JFK", "LGA"]


def test_a_place_without_airports_is_passed_over():
    locations = [{"full_name": "Some State", "type": "region", "airports": []}, place("Some City", "LGA")]
    matches, place_name = search_airports._to_matches(locations)
    assert place_name == "Some City" and [m.iata for m in matches] == ["LGA"]


def test_an_airport_unknown_to_airportsdata_keeps_its_code_with_no_country():
    matches, _ = search_airports._to_matches([place("Nowhere", "ZZZ")])
    assert [(m.iata, m.country) for m in matches] == [("ZZZ", "")]


def test_no_places_is_an_empty_result():
    assert search_airports._to_matches([]) == ([], "")


@pytest.mark.anyio
async def test_the_tool_returns_real_matches_not_blank_ones(monkeypatch, fake_cache):
    monkeypatch.setattr(search_airports, "_location_search", lambda query: new_york_locations())
    async with Client(mcp) as client:
        result = await client.call_tool("search_airports", {"query": "New York"})
    assert not result.is_error
    matches = result.structured_content["matches"]
    assert [m["iata"] for m in matches] == ["JFK", "LGA", "EWR"]
    assert all(all(value for value in m.values()) for m in matches), "no blank fields"
    assert "New York" in result.structured_content["message"]


@pytest.mark.anyio
async def test_an_unrecognized_shape_is_a_data_error_and_is_not_cached(monkeypatch, fake_cache):
    calls = []

    def stub(query):
        calls.append(query)
        return [{"full_name": "Mystery", "something_else": 1}]

    monkeypatch.setattr(search_airports, "_location_search", stub)
    async with Client(mcp) as client:
        for _ in range(2):
            result = await client.call_tool("search_airports", {"query": "Mystery"})
            assert result.is_error
            text = " ".join(part.text for part in result.content if hasattr(part, "text"))
            assert "UPSTREAM_DATA_ERROR" in text
    assert len(calls) == 2, "a failure must not be cached"


def test_a_response_without_a_locations_list_is_rejected(monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(200, json={"search_metadata": {}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(search_airports.httpx, "get", fake_get)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "synthetic-key-do-not-leak")
    with pytest.raises(ValueError):
        search_airports._location_search("New York")
