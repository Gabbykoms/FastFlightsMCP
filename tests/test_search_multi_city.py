"""Tests-first contract for `search_multi_city`: offline MCP calls, never a real SearchApi request.

The implementation must register the tool `search_multi_city` on fastflights_mcp.server.mcp and
call an async fastflights_mcp.server.fetch_multi_city_candidates(**query) adapter boundary
(raw SearchApi mapping is tested in tests/test_provider.py). The query it passes is
  legs (list of {origin, destination, depart_date, max_stops}), cabin, adults, children,
  infants_in_seat, infants_on_lap, currency
where each leg's max_stops is that leg's effective limit: its own value when given, otherwise
the top-level max_stops (None when neither is given). Budget and strict_budget stay in the tool.

Scope: complete-itinerary search for 2 to 6 legs. Customization (replacing one leg of a
returned trip) is deliberately not part of this contract yet, so there is no `customization`
argument and no STALE_SELECTION error; see search_multi_city.plan.md.

The tool re-checks every candidate before presenting it and drops what it cannot trust:
a candidate without exactly one leg per requested leg, a leg outside its airport sets, a leg
that departs before the previous leg arrives (elapsed time), a change of airport within a
city with less than 24 elapsed hours in between, or a leg over its stop limit. It recomputes
total_travel_minutes and total_stops from the legs, so time spent visiting a city between
legs never counts. Ranking, the 3+2 budget allocation, the inclusive 20% ceiling, strict
budgets and the one-hour price tie are the rules of search_flights, applied to the whole trip.
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

LINK = "https://www.google.com/travel/flights"


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
    monkeypatch.setattr(server, "fetch_multi_city_candidates", fake, raising=False)
    return fake


def day(offset):
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


def leg_request(origin, destination, offset, **extra):
    return {"origin": origin, "destination": destination, "depart_date": day(offset), **extra}


@pytest.fixture
def query():
    """New York to London, London to Paris, Paris to New York."""
    return {"legs": [leg_request(["JFK"], ["LHR"], 60), leg_request(["LHR"], ["CDG"], 64),
                     leg_request(["CDG"], ["JFK"], 70)],
            "cabin": "economy"}


def text_of(result):
    return " ".join(getattr(item, "text", "") for item in result.content)


async def call(server, query):
    async with Client(server.mcp) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "search_multi_city" in names, "search_multi_city is not registered yet"
        return await client.call_tool("search_multi_city", query)


def at(offset, hour):
    return datetime.fromisoformat(day(offset)).replace(hour=hour, tzinfo=timezone.utc)


def flight(origin, destination, departure, minutes=420, stops=0, number="TA100"):
    """Synthetic normalized leg, not a claimed provider fixture."""
    arrival = departure + timedelta(minutes=minutes)
    segment = {"airline": "Test Air", "flight_number": number, "origin": origin,
               "destination": destination, "departure": departure.isoformat(),
               "arrival": arrival.isoformat(), "duration_minutes": minutes}
    return {"origin": origin, "destination": destination, "departure": departure.isoformat(),
            "arrival": arrival.isoformat(), "duration_minutes": minutes, "stops": stops,
            "segments": [segment], "google_flights_url": LINK}


def trip(identifier, price, legs, total_minutes=None):
    return {"id": identifier, "legs": legs, "price_per_person": price, "currency": "USD",
            "total_travel_minutes": (sum(l["duration_minutes"] for l in legs)
                                     if total_minutes is None else total_minutes),
            "total_stops": sum(l["stops"] for l in legs), "google_flights_url": LINK,
            "separate_tickets": None}


def good(identifier, price, extra_minutes=0, stops=0):
    """The requested trip: JFK-LHR, LHR-CDG, CDG-JFK. `extra_minutes` lengthens leg 1."""
    return trip(identifier, price, [
        flight("JFK", "LHR", at(60, 20), 420 + extra_minutes, stops, f"{identifier}-1"),
        flight("LHR", "CDG", at(64, 10), 80, 0, f"{identifier}-2"),
        flight("CDG", "JFK", at(70, 10), 500, 0, f"{identifier}-3")])


async def successful(server, backend, query):
    result = await call(server, query)
    assert not result.is_error, text_of(result)
    backend.assert_awaited_once()
    data = result.structured_content
    assert len(data["results"]) <= 5
    assert data["message"] is None or isinstance(data["message"], str)
    if data["results"]:
        recommended = next(x for x in data["results"] if x["id"] == data["recommended_id"])
        assert recommended["recommendation_reasons"]
    else:
        assert data["recommended_id"] is None and data["message"]
    return data


# --- schema and validation -----------------------------------------------------------------

async def test_discovery_schema(server):
    async with Client(server.mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert "search_multi_city" in tools, "search_multi_city is not registered yet"
    tool = tools["search_multi_city"]
    schema = tool.input_schema
    assert set(schema["required"]) == {"legs", "cabin"}
    props = schema["properties"]
    assert props["legs"]["type"] == "array"
    assert props["legs"]["minItems"] == 2 and props["legs"]["maxItems"] == 6
    assert set(props["cabin"]["enum"]) == {"economy", "premium_economy", "business", "first"}
    assert all(name in props for name in (
        "adults", "children", "infants_in_seat", "infants_on_lap", "currency", "budget",
        "strict_budget", "max_stops"))
    assert "customization" not in props
    assert props["adults"]["minimum"] == 1 and props["adults"]["maximum"] == 9
    assert props["strict_budget"]["default"] is False and props["currency"]["default"] == "USD"
    assert tool.description and "search_airports" in tool.description
    leg = schema["$defs"][props["legs"]["items"]["$ref"].split("/")[-1]] \
        if "$ref" in props["legs"]["items"] else props["legs"]["items"]
    assert set(leg["required"]) == {"origin", "destination", "depart_date"}
    assert {"origin", "destination", "depart_date", "max_stops"} <= set(leg["properties"])
    for name in ("origin", "destination"):
        assert leg["properties"][name]["minItems"] == 1 and leg["properties"][name]["maxItems"] == 10


@pytest.mark.parametrize("patch", [
    {"legs": "JFK-LHR"}, {"legs": [{"origin": "JFK", "destination": "LHR", "depart_date": "x"}] * 2},
    {"cabin": 1}, {"adults": "2"}, {"adults": True}, {"strict_budget": "false"}, {"unknown": 1},
    {"legs": [{"origin": ["JFK"], "destination": ["LHR"], "depart_date": 20270105}] * 2},
    {"legs": [{"origin": ["JFK"], "destination": ["LHR"], "depart_date": "2027-01-05", "foo": 1},
              {"origin": ["LHR"], "destination": ["JFK"], "depart_date": "2027-01-09"}]},
])
async def test_wrong_types_never_execute_tool_body(server, backend, query, patch):
    entered = []
    previous = sys.getprofile()

    def trace(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "search_multi_city":
            entered.append(frame.f_code.co_filename)

    sys.setprofile(trace)
    try:
        result = await call(server, {**query, **patch})
    finally:
        sys.setprofile(previous)
    assert result.is_error
    assert not entered, "SDK validation must reject wrong types before tool execution"
    backend.assert_not_awaited()


def legs_with(query, index, **change):
    legs = [dict(leg) for leg in query["legs"]]
    legs[index].update(change)
    return {"legs": legs}


@pytest.mark.parametrize("make", [
    lambda q: {"legs": q["legs"][:1]},                                          # one leg
    lambda q: {"legs": q["legs"] * 3},                                          # nine legs
    lambda q: {"legs": q["legs"][:2] + [leg_request(["CDG"], ["JFK"], 70)] * 5},  # seven legs
    lambda q: legs_with(q, 0, origin=[]),
    lambda q: legs_with(q, 0, origin=["JFK"] * 11),
    lambda q: legs_with(q, 0, origin=["JFK", "JFK"]),
    lambda q: legs_with(q, 1, destination=["not-an-airport"]),
    lambda q: legs_with(q, 1, depart_date="2027-02-30"),
    lambda q: legs_with(q, 0, depart_date="2000-01-01"),
    lambda q: legs_with(q, 1, depart_date=day(59)),            # earlier than leg 1
    lambda q: legs_with(q, 1, max_stops=3),
    lambda q: legs_with(q, 1, max_stops=-1),
    lambda q: {"cabin": "luxury"}, lambda q: {"max_stops": 3}, lambda q: {"budget": 0},
    lambda q: {"budget": -1}, lambda q: {"strict_budget": True}, lambda q: {"adults": 0},
    lambda q: {"adults": 10}, lambda q: {"adults": 8, "children": 2},
    lambda q: {"infants_on_lap": 2}, lambda q: {"currency": "usd"},
])
async def test_invalid_inputs_rejected_before_upstream(server, backend, query, make):
    result = await call(server, {**query, **make(query)})
    assert result.is_error
    backend.assert_not_awaited()


@pytest.mark.parametrize("patch", [{"children": 1}, {"infants_in_seat": 1}, {"infants_on_lap": 1}])
async def test_children_and_infants_are_rejected_until_priced(server, backend, query, patch):
    result = await call(server, {**query, **patch})
    assert result.is_error and "UNSUPPORTED_FILTER" in text_of(result)
    backend.assert_not_awaited()


@pytest.mark.parametrize("legs", [
    # New York to London, then Paris to Rome: London and Paris are different cities
    [leg_request(["JFK"], ["LHR"], 60), leg_request(["CDG"], ["FCO"], 64)],
    # Boston is not New York
    [leg_request(["LHR"], ["JFK"], 60), leg_request(["BOS"], ["LHR"], 64)],
    # one airport of the earlier destination set is in another city
    [leg_request(["JFK"], ["LHR", "CDG"], 60), leg_request(["LHR"], ["FCO"], 64)],
    # a leg that stays in one city
    [leg_request(["JFK"], ["LGA"], 60), leg_request(["LGA"], ["LHR"], 64)],
])
async def test_route_gaps_are_rejected_before_upstream(server, backend, query, legs):
    result = await call(server, {**query, "legs": legs})
    assert result.is_error
    backend.assert_not_awaited()
    assert "city" in text_of(result).lower()


@pytest.mark.parametrize("legs", [
    [leg_request(["JFK", "EWR", "LGA"], ["LHR", "LGW"], 60), leg_request(["LGW", "LHR"], ["CDG", "ORY"], 64)],
    [leg_request(["LHR"], ["JFK"], 60), leg_request(["EWR"], ["LHR"], 64)],      # Newark is New York
    [leg_request(["LHR"], ["FCO"], 60), leg_request(["CIA"], ["LHR"], 64)],      # "Rome" and "Roma"
    [leg_request(["JFK"], ["LHR"], 60), leg_request(["LHR"], ["CDG"], 60)],      # same day is allowed
    [leg_request(["JFK"], ["LHR"], 60), leg_request(["STN"], ["CDG"], 64),
     leg_request(["ORY"], ["EWR"], 70), leg_request(["EWR"], ["LHR"], 72),
     leg_request(["LGW"], ["JFK"], 74), leg_request(["LGA"], ["LHR"], 76)],    # six legs
])
async def test_same_city_airport_sets_are_accepted(server, backend, query, legs):
    result = await call(server, {**query, "legs": legs})
    assert not result.is_error, text_of(result)
    backend.assert_awaited_once()


async def test_empty_is_success(server, backend, query):
    data = await successful(server, backend, query)
    assert data["results"] == [] and data["within_budget_found"] is None
    assert "complete" in data["message"].lower()


async def test_defaults_legs_and_effective_stop_limits_are_passed_on(server, backend, query):
    query["max_stops"] = 1
    query["legs"][1]["max_stops"] = 0
    data = await successful(server, backend, query)
    normalized = data["query"]
    assert [l["origin"] for l in normalized["legs"]] == [["JFK"], ["LHR"], ["CDG"]]
    assert normalized["adults"] == 1 and normalized["children"] == normalized["infants_in_seat"] == 0
    assert normalized["currency"] == "USD" and normalized["cabin"] == "economy"
    sent = backend.await_args.kwargs
    assert [l["max_stops"] for l in sent["legs"]] == [1, 0, 1]
    assert (sent["cabin"], sent["adults"], sent["currency"]) == ("economy", 1, "USD")
    assert "budget" not in sent and "strict_budget" not in sent


async def test_no_stop_limit_is_passed_as_none(server, backend, query):
    await successful(server, backend, query)
    assert [l["max_stops"] for l in backend.await_args.kwargs["legs"]] == [None, None, None]


# --- complete itineraries only -------------------------------------------------------------

async def test_a_complete_trip_carries_every_leg_and_the_whole_trip_price(server, backend, query):
    backend.return_value = [good("a", 900)]
    data = await successful(server, backend, {**query, "budget": 1000, "strict_budget": True})
    option, = data["results"]
    assert [(l["origin"], l["destination"]) for l in option["legs"]] == [
        ("JFK", "LHR"), ("LHR", "CDG"), ("CDG", "JFK")]
    assert option["price_per_person"] == 900 and option["budget_status"] == "within_budget"
    assert option["budget_overage"] == 0 and data["within_budget_found"] is True


@pytest.mark.parametrize("make", [
    lambda c: {**c, "legs": c["legs"][:2]},          # a leg is missing
    lambda c: {**c, "legs": c["legs"] + c["legs"][:1]},
    lambda c: {**c, "legs": c["legs"][:1]},
], ids=["missing-leg", "extra-leg", "one-leg"])
async def test_partial_trips_are_never_presented(server, backend, query, make):
    backend.return_value = [make(good("a", 900))]
    result = await call(server, query)
    # Dropped, or reported as malformed provider data; never shown as a trip.
    if result.is_error:
        assert "UPSTREAM_DATA_ERROR" in text_of(result)
    else:
        assert result.structured_content["results"] == []
        assert result.structured_content["recommended_id"] is None


async def test_one_unusable_trip_does_not_hide_a_good_one(server, backend, query):
    backend.return_value = [{**good("partial", 500), "legs": good("partial", 500)["legs"][:2]},
                            good("whole", 900)]
    data = await successful(server, backend, query)
    assert [x["id"] for x in data["results"]] == ["whole"]


async def test_a_leg_outside_its_requested_airports_is_dropped(server, backend, query):
    wrong = good("wrong", 500)
    wrong["legs"][1] = flight("LHR", "ORY", at(64, 10), 80, 0, "x")
    backend.return_value = [wrong, good("right", 900)]
    data = await successful(server, backend, query)
    assert [x["id"] for x in data["results"]] == ["right"]


async def test_a_leg_that_departs_before_the_previous_leg_lands_is_dropped(server, backend, query):
    early = good("early", 500)
    lands = datetime.fromisoformat(early["legs"][0]["arrival"])
    early["legs"][1] = flight("LHR", "CDG", lands - timedelta(minutes=1), 80, 0, "x")
    backend.return_value = [early, good("ok", 900)]
    data = await successful(server, backend, query)
    assert [x["id"] for x in data["results"]] == ["ok"]


@pytest.mark.parametrize("gap_minutes,kept", [(24 * 60 - 1, False), (24 * 60, True)])
async def test_changing_airport_in_a_city_needs_twenty_four_elapsed_hours(
        server, backend, query, gap_minutes, kept):
    query["legs"][1]["origin"] = ["LHR", "LGW"]
    query["legs"][0]["destination"] = ["LHR", "LGW"]
    landing = flight("JFK", "LGW", at(60, 20), 420, 0, "a")
    departure = datetime.fromisoformat(landing["arrival"]) + timedelta(minutes=gap_minutes)
    changed = trip("changed", 900, [landing, flight("LHR", "CDG", departure, 80, 0, "b"),
                                    flight("CDG", "JFK", at(70, 10), 500, 0, "c")])
    backend.return_value = [changed]
    result = await call(server, query)
    assert not result.is_error
    assert bool(result.structured_content["results"]) is kept


async def test_an_airport_change_is_disclosed(server, backend, query):
    query["legs"][1]["origin"] = ["LHR", "LGW"]
    query["legs"][0]["destination"] = ["LHR", "LGW"]
    landing = flight("JFK", "LGW", at(60, 20), 420, 0, "a")
    departure = datetime.fromisoformat(landing["arrival"]) + timedelta(days=3)
    backend.return_value = [trip("changed", 900, [landing, flight("LHR", "CDG", departure, 80, 0, "b"),
                                                  flight("CDG", "JFK", at(70, 10), 500, 0, "c")])]
    data = await successful(server, backend, query)
    message = (data["message"] or "").lower()
    assert "airport change" in message and "lgw" in message and "lhr" in message


# --- stop limits, duration and ranking -----------------------------------------------------

async def test_a_top_level_stop_limit_applies_to_every_leg(server, backend, query):
    backend.return_value = [good("direct", 900), good("one-stop", 800, stops=1)]
    data = await successful(server, backend, {**query, "max_stops": 0})
    assert [x["id"] for x in data["results"]] == ["direct"]


async def test_a_leg_limit_overrides_the_top_level_limit_for_that_leg_only(server, backend, query):
    one_stop_first = good("first-stop", 800, stops=1)           # connecting flight on leg 1
    one_stop_second = good("second-stop", 810)
    one_stop_second["legs"][1] = flight("LHR", "CDG", at(64, 10), 200, 1, "s")
    one_stop_second["total_stops"] = 1
    backend.return_value = [good("direct", 900), one_stop_first, one_stop_second]
    query["legs"][1]["max_stops"] = 1
    data = await successful(server, backend, {**query, "max_stops": 0})
    assert {x["id"] for x in data["results"]} == {"direct", "second-stop"}


async def test_trip_duration_counts_travel_and_connections_but_not_city_visits(server, backend, query):
    trip_ = good("a", 900)
    flying = sum(l["duration_minutes"] for l in trip_["legs"])
    trip_["total_travel_minutes"] = flying + 4 * 24 * 60      # a candidate that counted the visits
    backend.return_value = [trip_]
    data = await successful(server, backend, query)
    assert data["results"][0]["total_travel_minutes"] == flying


async def test_city_visits_are_not_connecting_stops(server, backend, query):
    trip_ = good("a", 900)
    trip_["total_stops"] = 2          # a candidate that counted the two city visits as stops
    backend.return_value = [trip_]
    data = await successful(server, backend, query)
    assert data["results"][0]["total_stops"] == 0


async def test_connection_waits_are_part_of_the_trip_time(server, backend, query):
    """A leg's duration already includes its connection waits (two hours flying, a three-hour
    wait and two more hours flying is seven hours, not four); that is the time that ranks."""
    connecting = good("connecting", 900)
    connecting["legs"][0] = flight("JFK", "LHR", at(60, 20), 7 * 60, 1, "c")
    nonstop = good("nonstop", 900, extra_minutes=-7 * 60 + 5 * 60)   # leg 1 of 5 hours
    backend.return_value = [connecting, nonstop]
    data = await successful(server, backend, query)
    assert data["recommended_id"] == "nonstop"


@pytest.mark.parametrize("difference,winner", [(59, "cheap"), (60, "cheap"), (61, "fast")])
async def test_one_hour_price_tiebreak(server, backend, query, difference, winner):
    backend.return_value = [good("fast", 1000), good("cheap", 900, extra_minutes=difference)]
    data = await successful(server, backend, query)
    assert data["recommended_id"] == winner
    assert all(x["budget_status"] == "not_specified" and x["budget_overage"] is None
               for x in data["results"])


async def test_shorter_trip_wins_over_fewer_stops(server, backend, query):
    backend.return_value = [good("short", 900, extra_minutes=-120, stops=2),
                            good("long", 900, extra_minutes=60, stops=1)]
    data = await successful(server, backend, query)
    assert data["recommended_id"] == "short"


# --- budget ----------------------------------------------------------------------------------

async def test_no_budget_returns_the_five_best_across_all_airports(server, backend, query):
    backend.return_value = [good(str(i), 900 + i * 10) for i in reversed(range(7))]
    data = await successful(server, backend, query)
    assert len(data["results"]) == 5 and data["recommended_id"] == "0"


async def test_budget_split_and_recommendation(server, backend, query):
    backend.return_value = [good(str(i), price, extra) for i, (price, extra) in enumerate([
        (900, 200), (910, 200), (920, 200), (930, 200), (1050, 0), (1100, 0), (1201, -100)])]
    data = await successful(server, backend, {**query, "budget": 1000})
    options = data["results"]
    assert len(options) == 5
    assert sum(x["budget_status"] == "within_budget" for x in options) == 3
    assert sum(x["budget_status"] == "over_budget" for x in options) == 2
    assert all(x["price_per_person"] <= 1200 for x in options)
    recommendation = next(x for x in options if x["id"] == data["recommended_id"])
    assert recommendation["price_per_person"] <= 1000


@pytest.mark.parametrize("price,expected", [(1200, ["only"]), (1201, [])])
async def test_inclusive_twenty_percent_ceiling_on_the_whole_trip(server, backend, query, price, expected):
    backend.return_value = [good("only", price)]
    data = await successful(server, backend, {**query, "budget": 1000})
    assert [x["id"] for x in data["results"]] == expected and data["within_budget_found"] is False


async def test_strict_budget_excludes_alternatives(server, backend, query):
    backend.return_value = [good("within", 1000, 200), good("over", 1001)]
    data = await successful(server, backend, {**query, "budget": 1000, "strict_budget": True})
    assert [x["id"] for x in data["results"]] == ["within"]


@pytest.mark.parametrize("within,over,expected_within,expected_over", [
    (6, 0, 5, 0), (6, 1, 4, 1), (2, 4, 2, 3), (0, 3, 0, 3),
])
async def test_unused_budget_slots(server, backend, query, within, over, expected_within, expected_over):
    backend.return_value = ([good(f"w{i}", 900 + i, 300) for i in range(within)]
                            + [good(f"o{i}", 1100 + i) for i in range(over)])
    data = await successful(server, backend, {**query, "budget": 1000})
    assert sum(x["budget_status"] == "within_budget" for x in data["results"]) == expected_within
    assert sum(x["budget_status"] == "over_budget" for x in data["results"]) == expected_over
    assert data["recommended_id"] == ("w0" if within else "o0")


# --- not exhaustive and upstream failures -------------------------------------------------

async def test_a_search_that_stopped_early_says_so(server, backend, query):
    class Stopped(list):
        exhaustive = False
        requests_used = 7

    backend.return_value = Stopped([good("a", 900)])
    data = await successful(server, backend, query)
    assert "exhaustive" in data["message"].lower()


async def test_a_complete_search_does_not_claim_otherwise(server, backend, query):
    backend.return_value = [good("a", 900)]
    data = await successful(server, backend, query)
    assert data["message"] is None


@pytest.mark.parametrize("status,category", [(401, "UPSTREAM_AUTH_ERROR"), (429, "RATE_LIMITED"),
                                              (503, "UPSTREAM_UNAVAILABLE")])
async def test_http_failures_are_actionable_and_sanitized(server, backend, query, status, category, caplog):
    secret = "synthetic-secret-do-not-return"
    request = httpx.Request("GET", f"https://www.searchapi.io/api/v1/search?api_key={secret}")
    backend.side_effect = httpx.HTTPStatusError(
        str(request.url), request=request, response=httpx.Response(status, request=request))
    result = await call(server, query)
    backend.assert_awaited_once()
    assert result.is_error
    message = text_of(result)
    assert category in message and message.replace(category, "").strip(" :.-\n")
    assert secret not in message + caplog.text and "api_key=" not in message + caplog.text


async def test_upstream_timeout_is_sanitized(server, backend, query):
    secret = "synthetic-secret-do-not-return"
    backend.side_effect = httpx.ReadTimeout(f"timeout https://www.searchapi.io/x?api_key={secret}")
    result = await call(server, query)
    assert result.is_error and "UPSTREAM_TIMEOUT" in text_of(result)
    assert secret not in text_of(result) and "api_key=" not in text_of(result)


async def test_all_three_tools_are_registered_together(server):
    async with Client(server.mcp) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert {"search_airports", "search_flights", "search_multi_city"} <= names
