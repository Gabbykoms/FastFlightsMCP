"""Tests-first contract for find_cheapest_dates (spec.md section 3.5).

Offline MCP calls through an in-memory Client: no model, no network, no real key.

The implementation must provide fastflights_mcp.tools.find_cheapest_dates, registered on
the shared `mcp`, with one seam tests replace:

    async _calendar_search(params: dict) -> dict
        The only function that calls SearchApi's google_flights_calendar engine. It
        receives the SearchApi query parameters (without the key) and returns the raw
        JSON response, whose `calendar` list holds entries like
        {"departure", "return", "price", "is_lowest_price", "has_no_flights"}.

Raw calendar responses below are synthetic, built from the documented response shape.
A sanitized real calendar recording is still needed before the provider mapping is
claimed to work (plan.md: Patrick owns the calendar fixture).
"""
import importlib
import socket
import sys
from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp import Client

pytestmark = pytest.mark.anyio

TOOL = "find_cheapest_dates"


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
    return importlib.import_module("fastflights_mcp.tools.find_cheapest_dates")


@pytest.fixture
def upstream(tool, monkeypatch):
    fake = AsyncMock(return_value={"calendar": []})
    monkeypatch.setattr(tool, "_calendar_search", fake)
    return fake


def day(offset):
    """An ISO date `offset` days after a start safely in the future."""
    return (date.today() + timedelta(days=60 + offset)).isoformat()


@pytest.fixture
def one_way():
    return {"origin": ["JFK"], "destination": ["LIS"], "trip_type": "one_way",
            "depart_start": day(0), "depart_end": day(6), "cabin": "economy"}


@pytest.fixture
def round_trip():
    return {"origin": ["JFK"], "destination": ["LIS"], "trip_type": "round_trip",
            "depart_start": day(0), "depart_end": day(2),
            "return_start": day(7), "return_end": day(9), "cabin": "economy"}


async def call(server, args):
    async with Client(server.mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert TOOL in names, f"{TOOL} is not registered yet"
        return await client.call_tool(TOOL, args)


def text(result):
    return " ".join(getattr(part, "text", "") for part in result.content)


def entry(depart, price=None, ret=None, lowest=False):
    """One synthetic raw calendar entry in SearchApi's documented shape."""
    item = {"departure": depart}
    if ret is not None:
        item["return"] = ret
    if price is None:
        item["has_no_flights"] = True
    else:
        item["price"] = price
    if lowest:
        item["is_lowest_price"] = True
    return item


# 1. Discovery and schema --------------------------------------------------------------

async def test_listed_with_schema_from_the_spec(server, tool):
    async with Client(server.mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert TOOL in tools, f"{TOOL} is not registered yet"
    listed = tools[TOOL]
    schema = listed.input_schema
    assert set(schema["required"]) == {
        "origin", "destination", "trip_type", "depart_start", "depart_end", "cabin"}
    props = schema["properties"]
    for name in ("origin", "destination"):
        assert props[name]["type"] == "array"
        assert (props[name]["minItems"], props[name]["maxItems"]) == (1, 10)
    assert set(props["trip_type"]["enum"]) == {"one_way", "round_trip"}
    assert set(props["cabin"]["enum"]) == {"economy", "premium_economy", "business", "first"}
    assert (props["limit"]["minimum"], props["limit"]["maximum"]) == (1, 10)
    assert props["limit"]["default"] == 5
    nights = next(o for o in props["nights"]["anyOf"] if o.get("type") == "integer")
    assert (nights["minimum"], nights["maximum"]) == (1, 365)
    for name in ("return_start", "return_end", "nights", "adults", "currency", "max_stops"):
        assert name in props, name
    # The model only reads the description: it must say when to call the tool.
    assert "flexible" in listed.description.lower()


# 2. Wrong types never reach the tool body or the provider -----------------------------

@pytest.mark.parametrize("patch", [
    {"origin": "JFK"}, {"depart_start": 20270101}, {"limit": "5"}, {"limit": True},
    {"nights": 2.5}, {"adults": "1"}, {"unknown": 1},
])
async def test_wrong_types_never_execute_tool_body(server, upstream, round_trip, patch):
    entered = []
    previous = sys.getprofile()

    def trace(frame, event, arg):
        if event == "call" and frame.f_code.co_name == TOOL:
            entered.append(frame.f_code.co_filename)

    sys.setprofile(trace)
    try:
        result = await call(server, {**round_trip, **patch})
    finally:
        sys.setprofile(previous)
    assert result.is_error
    assert not entered, "SDK validation must reject wrong types before the tool runs"
    upstream.assert_not_awaited()


# 3. Out-of-range and inconsistent windows are rejected before a paid request ----------

@pytest.mark.parametrize("trip,patch", [
    ("one_way", {"depart_end": day(-1)}),                                 # end before start
    ("one_way", {"return_start": day(7), "return_end": day(9)}),          # return on one-way
    ("one_way", {"nights": 7}),                                           # nights on one-way
    ("one_way", {"depart_start": "2000-01-01"}),                          # in the past
    ("one_way", {"depart_end": "2027-02-30"}),                            # not a real date
    ("one_way", {"depart_end": day(200)}),                                # 201 outbound days
    ("round_trip", {"return_start": None, "return_end": None}),           # no return window
    ("round_trip", {"return_end": day(6)}),                               # return end before start
    ("round_trip", {"depart_end": day(9), "return_end": day(27)}),        # 10 x 21 = 210 pairs
    ("round_trip", {"limit": 0}), ("round_trip", {"limit": 11}),
    ("round_trip", {"nights": 0}), ("round_trip", {"nights": 366}),
    ("round_trip", {"trip_type": "multi_city"}), ("round_trip", {"cabin": "luxury"}),
    ("round_trip", {"origin": []}), ("round_trip", {"origin": ["JFK"] * 11}),
    ("round_trip", {"destination": ["not-an-airport"]}),
    ("round_trip", {"currency": "usd"}),
])
async def test_invalid_requests_rejected_before_upstream(
        server, upstream, one_way, round_trip, trip, patch):
    base = one_way if trip == "one_way" else round_trip
    result = await call(server, {**base, **patch})
    assert result.is_error
    upstream.assert_not_awaited()


@pytest.mark.parametrize("patch", [
    {"children": 1}, {"infants_in_seat": 1}, {"infants_on_lap": 1},
])
async def test_children_and_infants_rejected_until_priced(server, upstream, round_trip, patch):
    """Same interim limitation as search_flights: the provider returns one party total."""
    result = await call(server, {**round_trip, **patch})
    assert result.is_error
    assert "UNSUPPORTED_FILTER" in text(result)
    upstream.assert_not_awaited()


@pytest.mark.parametrize("trip,patch", [
    ("one_way", {"depart_end": day(199)}),                                # exactly 200 days
    ("round_trip", {"depart_end": day(9), "return_end": day(26)}),        # 10 x 20 = 200 pairs
])
async def test_largest_allowed_window_is_one_search(
        server, upstream, one_way, round_trip, trip, patch):
    base = one_way if trip == "one_way" else round_trip
    result = await call(server, {**base, **patch})
    upstream.assert_awaited_once()  # never split into additional billed searches
    assert result.is_error or "options" in result.structured_content


# 4. Request mapping and the echoed query -----------------------------------------------

async def test_request_maps_to_calendar_parameters(server, upstream, round_trip):
    upstream.return_value = {"calendar": [entry(day(0), 500, day(7))]}
    args = {**round_trip, "origin": ["JFK", "EWR"], "cabin": "first", "max_stops": 0}
    result = await call(server, args)
    assert not result.is_error, text(result)
    params = upstream.await_args.args[0]
    assert params["engine"] == "google_flights_calendar"
    assert params["flight_type"] == "round_trip"
    assert params["departure_id"] == "JFK,EWR"
    assert params["arrival_id"] == "LIS"
    assert (params["outbound_date_start"], params["outbound_date_end"]) == (day(0), day(2))
    assert (params["return_date_start"], params["return_date_end"]) == (day(7), day(9))
    assert params["travel_class"] == "first_class"   # SearchApi's name for first
    assert params["stops"] == "nonstop"
    assert params["currency"] == "USD"
    assert "api_key" not in params


async def test_result_echoes_query_and_envelope(server, upstream, round_trip):
    upstream.return_value = {"calendar": [entry(day(0), 500, day(7))]}
    result = await call(server, {**round_trip, "nights": 7, "currency": "EUR"})
    assert not result.is_error, text(result)
    data = result.structured_content
    query = data["query"]
    assert (query["depart_start"], query["depart_end"]) == (day(0), day(2))
    assert (query["return_start"], query["return_end"]) == (day(7), day(9))
    assert query["nights"] == 7
    assert query["currency"] == "EUR"
    assert isinstance(data["cache_hit"], bool)
    assert data["fetched_at"]


# 5. Results: ordering, ties, limit, nights, and unavailable dates ---------------------

async def test_options_sorted_with_ties_flagged_and_limit_applied(server, upstream, one_way):
    upstream.return_value = {"calendar": [
        entry(day(0), 410), entry(day(1), 300, lowest=True), entry(day(2), 300, lowest=True),
        entry(day(3), 520), entry(day(4)), entry(day(5), 450), entry(day(6), 390),
    ]}
    result = await call(server, {**one_way, "limit": 3})
    assert not result.is_error, text(result)
    data = result.structured_content
    options = data["options"]
    assert [(o["depart_date"], o["price_per_person"]) for o in options] == [
        (day(1), 300), (day(2), 300), (day(6), 390)]
    assert [o["is_lowest_price"] for o in options] == [True, True, False]
    assert all(o["return_date"] is None for o in options)
    assert data["lowest_price"] == 300
    # Unavailable dates are reported even though they fall outside the top three.
    assert data["no_flights"] == [{"depart_date": day(4), "return_date": None}]


async def test_nights_keeps_only_matching_stays_after_departure(server, upstream, round_trip):
    upstream.return_value = {"calendar": [
        entry(day(0), 610, day(7)),     # 7 nights: kept
        entry(day(1), 480, day(8)),     # 7 nights: kept
        entry(day(0), 400, day(9)),     # 9 nights: filtered out by nights=7
        entry(day(2), 350, day(7)),     # 5 nights: filtered out
    ]}
    result = await call(server, {**round_trip, "nights": 7})
    assert not result.is_error, text(result)
    pairs = [(o["depart_date"], o["return_date"]) for o in result.structured_content["options"]]
    assert pairs == [(day(1), day(8)), (day(0), day(7))]
    assert result.structured_content["lowest_price"] == 480


async def test_no_priced_dates_is_a_normal_result(server, upstream, one_way):
    upstream.return_value = {"calendar": [entry(day(n)) for n in range(7)]}
    result = await call(server, one_way)
    assert not result.is_error, text(result)
    data = result.structured_content
    assert data["options"] == []
    assert data["lowest_price"] is None
    assert len(data["no_flights"]) == 7
    assert isinstance(data["message"], str) and data["message"]


# 6. Upstream failures are errors the model can act on, never empty successes ----------

async def test_empty_provider_response_is_not_reported_as_no_flights(server, upstream, one_way):
    """SearchApi answers an over-limit request with an empty response; an in-limit request
    that comes back empty is missing coverage, not evidence that no flights exist."""
    upstream.return_value = {}
    result = await call(server, one_way)
    assert result.is_error
    assert "UPSTREAM_DATA_ERROR" in text(result)


@pytest.mark.parametrize("status,category", [(401, "UPSTREAM_AUTH_ERROR"),
                                              (429, "RATE_LIMITED"),
                                              (503, "UPSTREAM_UNAVAILABLE")])
async def test_http_failures_are_actionable_and_sanitized(
        server, upstream, one_way, status, category, caplog):
    secret = "synthetic-secret-do-not-return"
    request = httpx.Request("GET", f"https://www.searchapi.io/api/v1/search?api_key={secret}")
    response = httpx.Response(status, request=request)
    upstream.side_effect = httpx.HTTPStatusError(str(request.url), request=request,
                                                 response=response)
    result = await call(server, one_way)
    upstream.assert_awaited_once()
    assert result.is_error
    message = text(result)
    assert category in message
    assert message.replace(category, "").strip(" :.-\n")
    assert secret not in message + caplog.text
    assert "api_key=" not in message + caplog.text


async def test_missing_key_is_a_configuration_error(server, tool, one_way):
    """The real seam runs here, with SEARCHAPI_API_KEY removed by the no_network fixture."""
    result = await call(server, one_way)
    assert result.is_error
    assert "CONFIGURATION_ERROR" in text(result)


async def test_calendar_search_sends_the_key_in_a_header(tool, monkeypatch):
    """A key in the query string reaches httpx's INFO log and HTTPStatusError text (#18)."""
    seen = {}

    async def fake_send(self, request, **kwargs):
        seen["url"], seen["headers"] = str(request.url), dict(request.headers)
        return httpx.Response(200, json={"calendar": []}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send)
    monkeypatch.setenv("SEARCHAPI_API_KEY", "synthetic-key-do-not-leak")
    await tool._calendar_search({"engine": "google_flights_calendar"})
    assert "synthetic-key-do-not-leak" not in seen["url"]
    assert "api_key" not in seen["url"]
    assert seen["headers"]["authorization"] == "Bearer synthetic-key-do-not-leak"
