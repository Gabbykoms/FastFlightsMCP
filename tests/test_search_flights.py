"""Tests-first contract: offline MCP calls, never a real SearchApi request.

The implementation must expose server.mcp and an async
server.fetch_flight_candidates(**query) adapter boundary. The adapter returns
normalized, complete itinerary candidates; raw SearchApi mapping needs separate
fixture tests once sanitized responses are available.
"""
import importlib
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not access the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)


@pytest.fixture
def server():
    # Import inside a fixture so every test is collected before the server exists.
    return importlib.import_module("server")


@pytest.fixture
def backend(server, monkeypatch):
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(server, "fetch_flight_candidates", fake)
    return fake


@pytest.fixture
def query():
    date = datetime.now(timezone.utc).date() + timedelta(days=60)
    return {"origin": ["JFK"], "destination": ["LIS"],
            "depart_date": date.isoformat(), "cabin": "economy"}


async def call(server, query):
    async with Client(server.mcp) as client:
        return await client.call_tool("search_flights", query)


def candidate(query, identifier, price, minutes=420, stops=0):
    """Synthetic normalized candidate, not a claimed real provider fixture."""
    departure = datetime.fromisoformat(query["depart_date"]).replace(
        hour=8, tzinfo=timezone.utc)
    arrival = departure + timedelta(minutes=minutes)
    segment = {"airline": "Test Air", "flight_number": "TA100",
               "origin": "JFK", "destination": "LIS",
               "departure": departure.isoformat(), "arrival": arrival.isoformat(),
               "duration_minutes": minutes}
    leg = {**{k: segment[k] for k in ("origin", "destination", "departure", "arrival")},
           "duration_minutes": minutes, "stops": stops,
           "segments": [segment], "google_flights_url": "https://www.google.com/travel/flights"}
    return {"id": identifier, "legs": [leg], "price_per_person": price,
            "currency": "USD", "total_travel_minutes": minutes, "total_stops": stops,
            "google_flights_url": leg["google_flights_url"], "separate_tickets": False}


async def test_discovery_schema(server):
    async with Client(server.mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    tool = tools["search_flights"]
    schema = tool.input_schema
    assert set(schema["required"]) == {"origin", "destination", "depart_date", "cabin"}
    props = schema["properties"]
    for name in ("origin", "destination"):
        assert props[name]["type"] == "array"
        assert props[name]["minItems"] == 1
        assert props[name]["maxItems"] == 10
    assert props["adults"]["minimum"] == 1
    assert props["adults"]["maximum"] == 9
    assert set(props["cabin"]["enum"]) == {"economy", "premium_economy", "business", "first"}
    assert all(name in props for name in (
        "return_date", "children", "infants_in_seat", "infants_on_lap",
        "currency", "budget", "strict_budget", "max_stops"))
    assert tool.description


@pytest.mark.parametrize("patch", [
    {"origin": "JFK"}, {"depart_date": 123}, {"adults": "2"},
    {"adults": True}, {"strict_budget": "false"}, {"unknown": 1},
])
async def test_wrong_types_never_execute_tool_body(server, backend, query, patch):
    entered = []
    previous = sys.getprofile()

    def trace(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "search_flights":
            entered.append(frame.f_code.co_filename)

    sys.setprofile(trace)
    try:
        result = await call(server, {**query, **patch})
    finally:
        sys.setprofile(previous)
    assert result.is_error
    assert not entered, "SDK validation must reject wrong types before tool execution"
    backend.assert_not_awaited()


@pytest.mark.parametrize("patch", [
    {"origin": []}, {"origin": ["JFK"] * 11}, {"origin": ["JFK", "JFK"]},
    {"destination": ["not-an-airport"]}, {"adults": 0}, {"adults": 10},
    {"adults": 8, "children": 2}, {"infants_on_lap": 2},
    {"cabin": "luxury"}, {"max_stops": 3}, {"budget": -1},
    {"strict_budget": True}, {"depart_date": "2027-02-30"},
    {"depart_date": "2000-01-01"},
])
async def test_invalid_inputs_rejected_before_upstream(server, backend, query, patch):
    result = await call(server, {**query, **patch})
    assert result.is_error
    backend.assert_not_awaited()


async def test_return_before_departure(server, backend, query):
    earlier = datetime.fromisoformat(query["depart_date"]).date() - timedelta(days=1)
    result = await call(server, {**query, "return_date": earlier.isoformat()})
    assert result.is_error
    backend.assert_not_awaited()


async def test_empty_is_success(server, backend, query):
    result = await call(server, query)
    assert not result.is_error
    data = result.structured_content
    assert data["results"] == []
    assert data["recommended_id"] is None
    assert data["message"]
    backend.assert_awaited_once()


async def test_defaults_and_airport_sets_are_preserved(server, backend, query):
    query["origin"] = ["JFK", "EWR", "LGA"]
    result = await call(server, query)
    assert not result.is_error
    normalized = result.structured_content["query"]
    assert normalized["origin"] == query["origin"]
    assert normalized["adults"] == 1
    assert normalized["children"] == 0
    assert normalized["infants_in_seat"] == normalized["infants_on_lap"] == 0
    assert normalized["currency"] == "USD"


async def test_upstream_timeout_is_sanitized(server, backend, query):
    secret = "synthetic-secret-do-not-return"
    backend.side_effect = httpx.ReadTimeout(
        f"timeout https://www.searchapi.io/api/v1/search?api_key={secret}")
    result = await call(server, query)
    assert result.is_error
    text = " ".join(getattr(item, "text", "") for item in result.content)
    assert "UPSTREAM_TIMEOUT" in text
    assert secret not in text and "api_key=" not in text


async def test_budget_split_and_recommendation(server, backend, query):
    backend.return_value = [candidate(query, str(i), price, minutes)
                            for i, (price, minutes) in enumerate([
                                (450, 600), (460, 600), (470, 600),
                                (480, 600), (550, 400), (600, 400), (601, 300)])]
    result = await call(server, {**query, "budget": 500})
    assert not result.is_error
    data = result.structured_content
    options = data["results"]
    assert len(options) == 5
    assert sum(x["budget_status"] == "within_budget" for x in options) == 3
    assert sum(x["budget_status"] == "over_budget" for x in options) == 2
    assert all(x["price_per_person"] <= 600 for x in options)
    recommendation = next(x for x in options if x["id"] == data["recommended_id"])
    assert recommendation["price_per_person"] <= 500
    assert recommendation["recommendation_reasons"]


async def test_strict_budget_excludes_alternative(server, backend, query):
    backend.return_value = [candidate(query, "within", 500, 600),
                            candidate(query, "over", 501, 300)]
    result = await call(server, {**query, "budget": 500, "strict_budget": True})
    assert not result.is_error
    assert [x["id"] for x in result.structured_content["results"]] == ["within"]


async def test_no_budget_combined_limit_and_price_tiebreak(server, backend, query):
    backend.return_value = [candidate(query, str(i), 400 + i * 10, 420)
                            for i in reversed(range(7))]
    result = await call(server, query)
    assert not result.is_error
    data = result.structured_content
    assert len(data["results"]) == 5
    assert data["recommended_id"] == "0"


async def test_outbound_alone_is_not_round_trip(server, backend, query):
    backend.return_value = [candidate(query, "outbound-only", 400)]
    return_date = datetime.fromisoformat(query["depart_date"]).date() + timedelta(days=7)
    result = await call(server, {**query, "return_date": return_date.isoformat()})
    # An incomplete candidate may be excluded or reported as malformed data,
    # but must never be presented as a complete round-trip fare.
    if not result.is_error:
        assert result.structured_content["results"] == []
        assert result.structured_content["recommended_id"] is None
