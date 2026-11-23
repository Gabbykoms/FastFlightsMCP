"""Tests-first contract: offline MCP calls, never a real SearchApi request.

The implementation must expose fastflights_mcp.server.mcp and an async
fastflights_mcp.server.fetch_flight_candidates(**query) adapter boundary. The adapter returns
normalized, complete itinerary candidates; raw SearchApi mapping needs separate
fixture tests once sanitized responses are available.
"""
import importlib
import socket
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp import Client

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
    return importlib.import_module("fastflights_mcp.server")


@pytest.fixture
def backend(server, monkeypatch):
    fake = AsyncMock(return_value=[])
    # Temporary injection permits protocol assertions before the adapter exists.
    # Success cases assert this fake is awaited; no tool implementation is supplied.
    monkeypatch.setattr(server, "fetch_flight_candidates", fake, raising=False)
    return fake


@pytest.fixture
def query():
    date = datetime.now(timezone.utc).date() + timedelta(days=60)
    return {"origin": ["JFK"], "destination": ["LIS"],
            "depart_date": date.isoformat(), "cabin": "economy"}


async def call(server, query):
    async with Client(server.mcp) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "search_flights" in names, "search_flights is not registered yet"
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
    assert "search_flights" in tools, "search_flights is not registered yet"
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
    assert props["depart_date"]["type"] == "string"
    for name in ("adults", "children", "infants_in_seat", "infants_on_lap"):
        assert props[name]["type"] == "integer"
        assert props[name]["minimum"] == (1 if name == "adults" else 0)
        assert props[name]["maximum"] == (9 if name == "adults" else 8)
        assert props[name]["default"] == (1 if name == "adults" else 0)
    assert props["strict_budget"]["type"] == "boolean"
    assert props["strict_budget"]["default"] is False
    assert props["currency"]["type"] == "string"
    assert props["currency"]["default"] == "USD"


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
    {"budget": 0},
    {"children": -1}, {"children": 9}, {"infants_in_seat": 9},
    {"infants_on_lap": -1}, {"currency": "usd"}, {"max_stops": -1},
    {"depart_date": "2000-01-01"},
])
async def test_invalid_inputs_rejected_before_upstream(server, backend, query, patch):
    result = await call(server, {**query, **patch})
    assert result.is_error
    backend.assert_not_awaited()


@pytest.mark.parametrize("patch", [
    {"children": 1}, {"infants_in_seat": 1}, {"infants_on_lap": 1},
    {"adults": 2, "children": 2, "infants_on_lap": 1},
])
async def test_children_and_infants_are_rejected_until_priced(server, backend, query, patch):
    """SearchApi returns one party total, and a child's fare can differ from an adult's
    (tests/fixtures/searchapi/README.md), so no per-person price can be derived yet."""
    result = await call(server, {**query, **patch})
    assert result.is_error
    text = " ".join(getattr(item, "text", "") for item in result.content)
    assert "UNSUPPORTED_FILTER" in text
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
    backend.assert_awaited_once()
    if result.is_error:
        assert "UPSTREAM_DATA_ERROR" in " ".join(
            getattr(item, "text", "") for item in result.content)
    else:
        assert result.structured_content["results"] == []
        assert result.structured_content["recommended_id"] is None


async def successful(server, backend, query):
    result = await call(server, query)
    assert not result.is_error
    backend.assert_awaited_once()
    data = result.structured_content
    assert isinstance(data["query"], dict)
    assert isinstance(data["cache_hit"], bool)
    fetched = datetime.fromisoformat(data["fetched_at"].replace("Z", "+00:00"))
    assert fetched.utcoffset() == timedelta(0)
    assert data["message"] is None or isinstance(data["message"], str)
    assert len(data["results"]) <= 5
    if data["results"]:
        recommended = next(x for x in data["results"] if x["id"] == data["recommended_id"])
        assert recommended["recommendation_reasons"]
    else:
        assert data["recommended_id"] is None
        assert data["message"]
    return data


def round_trip(query, identifier, price):
    outbound = candidate(query, identifier, price)
    inbound = candidate({**query, "depart_date": query["return_date"]}, "return", price)
    leg = inbound["legs"][0]
    leg["origin"], leg["destination"] = "LIS", "JFK"
    leg["segments"][0].update(origin="LIS", destination="JFK")
    outbound["legs"].append(leg)
    outbound["total_travel_minutes"] += inbound["total_travel_minutes"]
    return outbound


async def test_complete_round_trip_uses_whole_trip_price(server, backend, query):
    query["return_date"] = (datetime.fromisoformat(query["depart_date"]) + timedelta(days=7)).date().isoformat()
    backend.return_value = [round_trip(query, "complete", 500)]
    data = await successful(server, backend, {**query, "budget": 500, "strict_budget": True})
    option, = data["results"]
    assert len(option["legs"]) == 2
    assert option["legs"][1]["origin"] == "LIS"
    assert option["legs"][1]["destination"] == "JFK"
    assert option["price_per_person"] == 500
    assert option["budget_status"] == "within_budget"
    assert option["budget_overage"] == 0
    assert data["within_budget_found"] is True


@pytest.mark.parametrize("difference,winner", [(59, "cheap"), (60, "cheap"), (61, "fast")])
async def test_one_hour_price_tiebreak(server, backend, query, difference, winner):
    backend.return_value = [candidate(query, "fast", 500, 360),
                            candidate(query, "cheap", 450, 360 + difference)]
    data = await successful(server, backend, query)
    assert data["recommended_id"] == winner
    assert data["within_budget_found"] is None
    assert all(x["budget_status"] == "not_specified" and x["budget_overage"] is None
               for x in data["results"])


@pytest.mark.parametrize("within,over,expected_within,expected_over", [
    (6, 0, 5, 0), (6, 1, 4, 1), (2, 4, 2, 3), (0, 3, 0, 3),
])
async def test_unused_budget_slots(server, backend, query, within, over, expected_within, expected_over):
    backend.return_value = ([candidate(query, f"w{i}", 450+i, 600) for i in range(within)]
                            + [candidate(query, f"o{i}", 550+i, 400) for i in range(over)])
    data = await successful(server, backend, {**query, "budget": 500})
    assert sum(x["budget_status"] == "within_budget" for x in data["results"]) == expected_within
    assert sum(x["budget_status"] == "over_budget" for x in data["results"]) == expected_over
    assert data["within_budget_found"] is bool(within)
    assert data["recommended_id"] == ("w0" if within else "o0")
    if not within:
        option = next(x for x in data["results"] if x["id"] == data["recommended_id"])
        assert option["budget_overage"] == 50


async def test_worse_over_budget_options_are_excluded(server, backend, query):
    backend.return_value = [candidate(query, f"w{i}", 450+i, 360) for i in range(5)]
    backend.return_value += [candidate(query, "worse", 550, 480)]
    data = await successful(server, backend, {**query, "budget": 500})
    assert {x["id"] for x in data["results"]} == {f"w{i}" for i in range(5)}


@pytest.mark.parametrize("price,expected", [(600, ["only"]), (601, [])])
async def test_inclusive_twenty_percent_ceiling(server, backend, query, price, expected):
    backend.return_value = [candidate(query, "only", price)]
    data = await successful(server, backend, {**query, "budget": 500})
    assert [x["id"] for x in data["results"]] == expected
    assert data["within_budget_found"] is False


def connecting_candidate(query, identifier, price, minutes, stops):
    option = candidate(query, identifier, price, minutes, stops)
    leg = option["legs"][0]
    airports = ["JFK", *["BOS", "LHR"][:stops], "LIS"]
    start = datetime.fromisoformat(leg["departure"])
    flight_minutes = (minutes - 30 * stops) // (stops + 1)
    segments = []
    for i in range(stops + 1):
        end = (datetime.fromisoformat(leg["arrival"]) if i == stops
               else start + timedelta(minutes=flight_minutes))
        segments.append({"airline": "Test Air", "flight_number": f"TA{i+100}",
                         "origin": airports[i], "destination": airports[i+1],
                         "departure": start.isoformat(), "arrival": end.isoformat(),
                         "duration_minutes": int((end-start).total_seconds()/60)})
        start = end + timedelta(minutes=30)
    leg["segments"] = segments
    return option


async def test_nonstop_filter_applies_to_both_budget_groups(server, backend, query):
    backend.return_value = [candidate(query, "direct", 450, 600),
                            candidate(query, "better-direct", 550, 400),
                            connecting_candidate(query, "cheap-stop", 400, 350, 1),
                            connecting_candidate(query, "over-stop", 560, 300, 1)]
    data = await successful(server, backend, {**query, "budget": 500, "max_stops": 0})
    assert {x["id"] for x in data["results"]} == {"direct", "better-direct"}
    assert data["recommended_id"] == "direct"


async def test_shorter_trip_wins_over_fewer_stops(server, backend, query):
    backend.return_value = [connecting_candidate(query, "short", 450, 480, 2),
                            connecting_candidate(query, "long", 450, 600, 1)]
    data = await successful(server, backend, query)
    assert data["recommended_id"] == "short"


@pytest.mark.parametrize("status,category", [(401, "UPSTREAM_AUTH_ERROR"),
                                              (429, "RATE_LIMITED"),
                                              (503, "UPSTREAM_UNAVAILABLE")])
async def test_http_failures_are_actionable_and_sanitized(server, backend, query, status, category, caplog):
    secret = "synthetic-secret-do-not-return"
    request = httpx.Request("GET", f"https://www.searchapi.io/api/v1/search?api_key={secret}")
    response = httpx.Response(status, request=request)
    backend.side_effect = httpx.HTTPStatusError(str(request.url), request=request, response=response)
    result = await call(server, query)
    backend.assert_awaited_once()
    assert result.is_error
    message = " ".join(getattr(item, "text", "") for item in result.content)
    assert category in message
    assert message.replace(category, "").strip(" :.-\n")
    assert secret not in message + caplog.text
    assert "api_key=" not in message + caplog.text
