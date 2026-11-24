"""Tests-first contract for get_price_insight (spec.md section 3.6).

Offline MCP calls through an in-memory Client: no model, no network, no real key.

The implementation must provide fastflights_mcp.tools.get_price_insight, registered on
the shared `mcp`, with one seam tests replace:

    async _insight_search(params: dict) -> dict
        The only function that calls SearchApi's google_flights engine for this tool.
        It receives the SearchApi query parameters (without the key) and returns the
        raw JSON response, whose optional `price_insights` object holds
        lowest_price, price_level, typical_price_range, price_history, and
        cheapest_to_book.

The main cases use the real, sanitized recordings in tests/fixtures/searchapi/
(JFK to LIS, recorded 2026-11-23): 61 daily history points from 2026-09-23 to
2026-11-22, price_level "typical".
"""
import copy
import importlib
import json
import socket
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp import Client

pytestmark = pytest.mark.anyio

TOOL = "get_price_insight"
FIXTURES = Path(__file__).parent / "fixtures" / "searchapi"


def recording(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


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
    # Imported inside fixtures so every test is collected (and fails on its own)
    # before the tool module exists.
    return importlib.import_module("fastflights_mcp.server")


@pytest.fixture
def tool(server):
    return importlib.import_module("fastflights_mcp.tools.get_price_insight")


@pytest.fixture
def upstream(tool, monkeypatch):
    fake = AsyncMock(return_value=recording("one_way"))
    monkeypatch.setattr(tool, "_insight_search", fake)
    return fake


@pytest.fixture
def query():
    depart = date.today() + timedelta(days=60)
    return {"origin": ["JFK"], "destination": ["LIS"],
            "depart_date": depart.isoformat(), "cabin": "economy"}


async def call(server, args):
    async with Client(server.mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert TOOL in names, f"{TOOL} is not registered yet"
        return await client.call_tool(TOOL, args)


def text(result):
    return " ".join(getattr(part, "text", "") for part in result.content)


# 1. Discovery and schema --------------------------------------------------------------

async def test_listed_with_schema_from_the_spec(server, tool):
    async with Client(server.mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert TOOL in tools, f"{TOOL} is not registered yet"
    listed = tools[TOOL]
    schema = listed.input_schema
    assert set(schema["required"]) == {"origin", "destination", "depart_date", "cabin"}
    props = schema["properties"]
    for name in ("origin", "destination"):
        assert props[name]["type"] == "array"
        assert (props[name]["minItems"], props[name]["maxItems"]) == (1, 10)
    assert set(props["cabin"]["enum"]) == {"economy", "premium_economy", "business", "first"}
    comparison = props["comparison_dates"]
    assert comparison["type"] == "array"
    assert comparison.get("maxItems") == 10
    for name in ("return_date", "adults", "currency", "max_stops"):
        assert name in props, name
    # The model only reads the description: it must say when to call the tool, and
    # that this is not a forecast.
    description = listed.description.lower()
    assert "price" in description
    assert "forecast" in description or "predict" in description


# 2. Wrong types never reach the tool body or the provider -----------------------------

@pytest.mark.parametrize("patch", [
    {"origin": "JFK"}, {"depart_date": 20270121}, {"comparison_dates": "2026-11-08"},
    {"comparison_dates": [20261108]}, {"adults": "1"}, {"adults": True}, {"unknown": 1},
])
async def test_wrong_types_never_execute_tool_body(server, upstream, query, patch):
    entered = []
    previous = sys.getprofile()

    def trace(frame, event, arg):
        if event == "call" and frame.f_code.co_name == TOOL:
            entered.append(frame.f_code.co_filename)

    sys.setprofile(trace)
    try:
        result = await call(server, {**query, **patch})
    finally:
        sys.setprofile(previous)
    assert result.is_error
    assert not entered, "SDK validation must reject wrong types before the tool runs"
    upstream.assert_not_awaited()


# 3. Out-of-range values are rejected before a billed search ----------------------------

def _tomorrow():
    return (date.today() + timedelta(days=1)).isoformat()


@pytest.mark.parametrize("patch", [
    {"comparison_dates": [f"2026-10-{d:02d}" for d in range(1, 12)]},   # 11 dates (max 10)
    {"comparison_dates": ["2026-11-08", "2026-11-08"]},                 # duplicate
    {"comparison_dates": "TOMORROW"},                                   # future observation
    {"comparison_dates": ["2026-02-30"]},                               # not a real date
    {"depart_date": "2000-01-01"},                                      # travel in the past
    {"return_date": "DAY_BEFORE"},                                      # return before departure
    {"origin": []}, {"origin": ["JFK"] * 11}, {"destination": ["not-an-airport"]},
    {"cabin": "luxury"}, {"adults": 0}, {"adults": 10}, {"currency": "usd"},
    {"max_stops": 3},
])
async def test_invalid_requests_rejected_before_upstream(server, upstream, query, patch):
    args = {**query, **patch}
    if args.get("comparison_dates") == "TOMORROW":
        args["comparison_dates"] = [_tomorrow()]
    if args.get("return_date") == "DAY_BEFORE":
        args["return_date"] = (date.fromisoformat(query["depart_date"])
                               - timedelta(days=1)).isoformat()
    result = await call(server, args)
    assert result.is_error
    upstream.assert_not_awaited()


@pytest.mark.parametrize("patch", [
    {"children": 1}, {"infants_in_seat": 1}, {"infants_on_lap": 1},
])
async def test_children_and_infants_rejected_until_priced(server, upstream, query, patch):
    """The per-person price basis for mixed parties is unverified, so it is reported
    explicitly rather than relabelling a party total (same rule as search_flights)."""
    result = await call(server, {**query, **patch})
    assert result.is_error
    assert "UNSUPPORTED_FILTER" in text(result)
    upstream.assert_not_awaited()


# 4. Request mapping --------------------------------------------------------------------

async def test_one_way_request_maps_to_flight_parameters(server, upstream, query):
    args = {**query, "origin": ["JFK", "EWR"], "cabin": "first", "max_stops": 0}
    result = await call(server, args)
    assert not result.is_error, text(result)
    upstream.assert_awaited_once()   # one combined search, never one per airport
    params = upstream.await_args.args[0]
    assert params["engine"] == "google_flights"
    assert params["flight_type"] == "one_way"
    assert params["departure_id"] == "JFK,EWR"
    assert params["arrival_id"] == "LIS"
    assert params["outbound_date"] == query["depart_date"]
    assert "return_date" not in params
    assert params["travel_class"] == "first_class"   # SearchApi's name for first
    assert params["stops"] == "nonstop"
    assert params["currency"] == "USD"
    assert "api_key" not in params


async def test_round_trip_request_includes_return_date(server, upstream, query):
    upstream.return_value = recording("round_trip_outbound")
    ret = (date.fromisoformat(query["depart_date"]) + timedelta(days=7)).isoformat()
    result = await call(server, {**query, "return_date": ret})
    assert not result.is_error, text(result)
    params = upstream.await_args.args[0]
    assert params["flight_type"] == "round_trip"
    assert params["return_date"] == ret
    assert result.structured_content["lowest_price"] == 676


# 5. Results from the real recording ---------------------------------------------------

async def test_insight_preserved_from_real_recording(server, upstream, query):
    result = await call(server, query)
    assert not result.is_error, text(result)
    data = result.structured_content
    assert data["available"] is True
    assert data["lowest_price"] == 404
    assert data["price_level"] == "typical"          # the provider's signal, unchanged
    assert data["typical_range"] == {"low": 240, "high": 420}
    assert data["query"]["depart_date"] == query["depart_date"]
    assert isinstance(data["cache_hit"], bool)
    assert data["fetched_at"]


async def test_history_is_every_observation_in_order(server, upstream, query):
    result = await call(server, query)
    history = result.structured_content["history"]
    assert len(history) == 61
    assert history[0] == {"observed_date": "2026-09-23", "price": 361}
    assert history[-1] == {"observed_date": "2026-11-22", "price": 404}
    dates = [h["observed_date"] for h in history]
    assert dates == sorted(dates)


async def test_comparisons_match_exact_dates_and_never_interpolate(server, upstream, query):
    result = await call(server, {**query,
                                 "comparison_dates": ["2026-11-08", "2026-09-01"]})
    assert not result.is_error, text(result)
    comparisons = result.structured_content["comparisons"]
    assert comparisons == [
        {"requested_date": "2026-11-08", "observed_date": "2026-11-08",
         "price": 404, "status": "matched"},
        # Before the history starts: unavailable, not an estimate.
        {"requested_date": "2026-09-01", "observed_date": None,
         "price": None, "status": "unavailable"},
    ]


async def test_estimated_savings_are_left_out(server, upstream, query):
    """The recording includes cheapest_to_book.estimated_savings; the spec omits it."""
    result = await call(server, query)
    rendered = json.dumps(result.structured_content)
    assert "estimated_savings" not in rendered
    assert "cheapest_to_book" not in rendered


async def test_notice_says_history_is_not_a_forecast(server, upstream, query):
    result = await call(server, query)
    notice = result.structured_content["notice"].lower()
    assert "confirm" in notice
    assert "predict" in notice or "forecast" in notice


async def test_unfamiliar_price_level_is_passed_through(server, upstream, query):
    """Only "typical" has been seen; the vocabulary is unverified, so no enum yet."""
    response = recording("one_way")
    response["price_insights"]["price_level"] = "high"
    upstream.return_value = response
    result = await call(server, query)
    assert result.structured_content["price_level"] == "high"


# 6. No insight is a normal answer; a partial insight stays partial ---------------------

async def test_no_insight_is_a_normal_result(server, upstream, query):
    response = recording("one_way")
    del response["price_insights"]
    upstream.return_value = response
    result = await call(server, {**query, "comparison_dates": ["2026-11-08"]})
    assert not result.is_error, text(result)
    data = result.structured_content
    assert data["available"] is False
    assert (data["lowest_price"], data["price_level"], data["typical_range"]) == (None, None, None)
    assert data["history"] == []
    assert data["comparisons"][0]["status"] == "unavailable"
    assert isinstance(data["message"], str) and data["message"]
    assert data["notice"]


async def test_partial_insight_keeps_missing_fields_null(server, upstream, query):
    response = recording("one_way")
    response["price_insights"] = {"lowest_price": 404}
    upstream.return_value = response
    result = await call(server, query)
    assert not result.is_error, text(result)
    data = result.structured_content
    assert data["available"] is True
    assert data["lowest_price"] == 404
    assert (data["price_level"], data["typical_range"]) == (None, None)
    assert data["history"] == []
    assert isinstance(data["message"], str) and data["message"]


# 7. A failed lookup is an error, not "no insight" -------------------------------------

@pytest.mark.parametrize("status,category", [(401, "UPSTREAM_AUTH_ERROR"),
                                              (429, "RATE_LIMITED"),
                                              (503, "UPSTREAM_UNAVAILABLE")])
async def test_http_failures_are_actionable_and_sanitized(
        server, upstream, query, status, category, caplog):
    secret = "synthetic-secret-do-not-return"
    request = httpx.Request("GET", f"https://www.searchapi.io/api/v1/search?api_key={secret}")
    response = httpx.Response(status, request=request)
    upstream.side_effect = httpx.HTTPStatusError(str(request.url), request=request,
                                                 response=response)
    result = await call(server, query)
    upstream.assert_awaited_once()
    assert result.is_error
    message = text(result)
    assert category in message
    assert message.replace(category, "").strip(" :.-\n")
    assert secret not in message + caplog.text
    assert "api_key=" not in message + caplog.text


async def test_malformed_history_is_an_upstream_data_error(server, upstream, query):
    response = recording("one_way")
    response["price_insights"]["price_history"] = [{"price": "cheap", "iso_date": "soon"}]
    upstream.return_value = response
    result = await call(server, query)
    assert result.is_error
    assert "UPSTREAM_DATA_ERROR" in text(result)


async def test_missing_key_is_a_configuration_error(server, tool, query):
    """The real seam runs here, with SEARCHAPI_API_KEY removed by the no_network fixture."""
    result = await call(server, query)
    assert result.is_error
    assert "CONFIGURATION_ERROR" in text(result)


async def test_insight_search_sends_the_key_in_a_header(tool, monkeypatch):
    """A key in the query string reaches httpx's INFO log and HTTPStatusError text (#18)."""
    seen = {}

    async def fake_send(self, request, **kwargs):
        seen["url"], seen["headers"] = str(request.url), dict(request.headers)
        return httpx.Response(200, json=copy.deepcopy(recording("one_way")), request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "synthetic-key-do-not-leak")
    await tool._insight_search({"engine": "google_flights"})
    assert "synthetic-key-do-not-leak" not in seen["url"]
    assert "api_key" not in seen["url"]
    assert seen["headers"]["authorization"] == "Bearer synthetic-key-do-not-leak"
