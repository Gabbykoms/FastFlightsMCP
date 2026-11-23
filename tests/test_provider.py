"""Tests-first contract for the SearchApi adapter, `fastflights_mcp.provider`.

Offline: every request goes to an httpx.MockTransport that serves the sanitized real
recordings in tests/fixtures/searchapi/ (see its README). Responses built by changing
those recordings are labelled synthetic. Nothing here is provider-verified beyond what
the recordings show.

Interface the implementation must provide
-----------------------------------------
fastflights_mcp.provider
  MAX_REQUESTS_PER_CALL = 10   attempted upstream requests per call, retries included
  MAX_RETRIES = 1              retries of one request, on HTTP 5xx only
  RETRY_DELAY_SECONDS          0 < delay <= 5, awaited through `_sleep(seconds)`
  REQUEST_TIMEOUT_SECONDS = 20 finite timeout on every request
  _TRANSPORT = None            test hook: an httpx transport used instead of the network
  async _sleep(seconds)        test hook for the retry delay
  class UnusableOption(ValueError)
  map_leg(option, google_flights_url) -> leg dict   one SearchApi option to one leg
  class Candidates(list)       .exhaustive (bool) and .requests_used (int)
  async fetch_flight_candidates(**query) -> Candidates
      query: origin, destination (airport lists), depart_date, return_date, cabin, adults,
      children, infants_in_seat, infants_on_lap, currency. Reads SEARCHAPI_API_KEY.
      HTTP and transport failures propagate as httpx exceptions (the tool maps them);
      a missing key raises ToolError("CONFIGURATION_ERROR ...") and unusable provider
      data raises ToolError("UPSTREAM_DATA_ERROR ...").
fastflights_mcp.server.fetch_flight_candidates delegates to the provider.
The search_flights tool adds a message containing "exhaustive" when the result is
not exhaustive.

Pricing: SearchApi's `price` is the party total. For adults-only parties the candidate's
price_per_person is price / adults. Parties with children or infants are deliberately
not specified here; that decision is still open. Recorded evidence: with one child, four
options cost 2x the adult price but one (AT 201 + AT 980) costs 993 instead of 1042, so
a child's fare can differ and a party total cannot be divided by headcount.
"""
import copy
import importlib
import json
import logging
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures" / "searchapi"
KEY = "synthetic-searchapi-key-do-not-leak"
LINK = "https://www.google.com/travel/flights"
MISSING = object()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def options(response):
    return (response.get("best_flights") or []) + (response.get("other_flights") or [])


def flight_numbers(option):
    return tuple(segment["flight_number"] for segment in option["flights"])


def numbers(candidate, leg=0):
    return tuple(s["flight_number"] for s in candidate["legs"][leg]["segments"])


def ok(body):
    return httpx.Response(200, json=body)


def status(code, body=None):
    reply = {"error": "synthetic provider error"} if body is None else body
    return lambda request: httpx.Response(code, json=reply)


def raising(exc):
    def respond(request):
        raise exc
    return respond


def no_options(response):
    """Synthetic: the real response shape with no flight options in it."""
    return {k: v for k, v in response.items() if k not in ("best_flights", "other_flights")}


def first_outbound():
    return options(load("round_trip_outbound"))[0]


def many_outbound(count):
    """Synthetic: the real outbound response padded, by copying options, to `count` options."""
    body = load("round_trip_outbound")
    real = options(body)
    padded = []
    for i in range(count):
        option = copy.deepcopy(real[i % len(real)])
        option["departure_token"] = f"synthetic-token-{i}"
        padded.append(option)
    return {**body, "best_flights": [], "other_flights": padded}


def respond_many(count):
    def respond(request):
        if "departure_token" in request.url.params:
            return ok(load("round_trip_return"))
        return ok(many_outbound(count))
    return respond


def fixture_respond(request):
    """Serve the recordings. Only the first outbound option has a recorded follow-up; the
    other follow-ups get a synthetic response with no return options."""
    params = request.url.params
    token = params.get("departure_token")
    if token is not None:
        returns = load("round_trip_return")
        return ok(returns if token == first_outbound()["departure_token"] else no_options(returns))
    if params["flight_type"] == "one_way":
        return ok(load("one_way_2_adults" if params["adults"] == "2" else "one_way"))
    return ok(load("round_trip_outbound"))


ALL_OPTIONS = [
    pytest.param(option, id=f"{name}:{'+'.join(flight_numbers(option)).replace(' ', '')}")
    for name in ("one_way", "round_trip_outbound", "round_trip_return")
    for option in options(load(name))
]


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not access the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)


@pytest.fixture
def provider():
    # Imported inside a fixture so every test is collected before the module exists.
    return importlib.import_module("fastflights_mcp.provider")


@pytest.fixture
def api(provider, monkeypatch):
    class FakeSearchApi:
        def __init__(self):
            self.requests = []
            self.sleeps = []
            self.respond = fixture_respond

    fake = FakeSearchApi()

    def handler(request):
        fake.requests.append(request)
        return fake.respond(request)

    async def sleep(seconds):
        fake.sleeps.append(seconds)

    monkeypatch.setattr(provider, "_TRANSPORT", httpx.MockTransport(handler), raising=False)
    monkeypatch.setattr(provider, "_sleep", sleep, raising=False)
    monkeypatch.setenv("SEARCHAPI_API_KEY", KEY)
    return fake


@pytest.fixture
def query():
    return {"origin": ["JFK"], "destination": ["LIS"], "depart_date": "2027-01-21",
            "return_date": None, "cabin": "economy", "adults": 1, "children": 0,
            "infants_in_seat": 0, "infants_on_lap": 0, "currency": "USD"}


@pytest.fixture
def round_trip_query(query):
    return {**query, "return_date": "2027-01-28"}


# --- Group 1: mapping real options to the tool's itinerary shape ------------------------

async def test_one_way_options_map_to_the_tool_shape(provider, api, query):
    result = await provider.fetch_flight_candidates(**query)
    assert len(result) == len(options(load("one_way"))) == 5
    for candidate in result:
        assert set(candidate) == {"id", "legs", "price_per_person", "currency",
                                  "total_travel_minutes", "total_stops",
                                  "google_flights_url", "separate_tickets"}
        assert candidate["separate_tickets"] is None
        assert candidate["currency"] == "USD"
        for leg in candidate["legs"]:
            assert set(leg) == {"origin", "destination", "departure", "arrival",
                                "duration_minutes", "stops", "segments", "google_flights_url"}
            for segment in leg["segments"]:
                assert set(segment) == {"airline", "flight_number", "origin", "destination",
                                        "departure", "arrival", "duration_minutes"}


async def test_nonstop_option_gets_utc_offsets(provider, api, query):
    result = await provider.fetch_flight_candidates(**query)
    candidate = next(c for c in result if numbers(c) == ("TP 210",))
    leg, = candidate["legs"]
    segment, = leg["segments"]
    assert segment["airline"] == "Tap Air Portugal"
    assert (segment["origin"], segment["destination"]) == ("JFK", "LIS")
    assert segment["departure"] == "2027-01-21T22:00:00-05:00"
    assert segment["arrival"] == "2027-01-22T10:00:00+00:00"
    assert segment["duration_minutes"] == 420
    assert leg["departure"] == segment["departure"] and leg["arrival"] == segment["arrival"]
    assert leg["duration_minutes"] == candidate["total_travel_minutes"] == 420
    assert leg["stops"] == candidate["total_stops"] == 0
    assert candidate["price_per_person"] == 404
    link = load("one_way")["search_metadata"]["request_url"]
    assert candidate["google_flights_url"] == leg["google_flights_url"] == link


@pytest.mark.parametrize("option", ALL_OPTIONS)
def test_every_option_maps_consistently_with_the_provider_times(provider, option):
    leg = provider.map_leg(option, LINK)
    segments = leg["segments"]
    assert len(segments) == len(option["flights"])
    assert leg["stops"] == len(segments) - 1 == len(option.get("layovers", []))
    assert leg["duration_minutes"] == option["total_duration"]
    assert (leg["origin"], leg["destination"]) == (segments[0]["origin"], segments[-1]["destination"])
    assert (leg["departure"], leg["arrival"]) == (segments[0]["departure"], segments[-1]["arrival"])
    assert leg["google_flights_url"] == LINK
    for raw, mapped in zip(option["flights"], segments):
        assert (mapped["airline"], mapped["flight_number"]) == (raw["airline"], raw["flight_number"])
        assert (mapped["origin"], mapped["destination"]) == (
            raw["departure_airport"]["id"], raw["arrival_airport"]["id"])
        assert mapped["duration_minutes"] == raw["duration"]
        elapsed = datetime.fromisoformat(mapped["arrival"]) - datetime.fromisoformat(mapped["departure"])
        assert elapsed.total_seconds() / 60 == raw["duration"], "offsets must reproduce the provider's duration"
    for earlier, later, layover in zip(segments, segments[1:], option.get("layovers", [])):
        gap = datetime.fromisoformat(later["departure"]) - datetime.fromisoformat(earlier["arrival"])
        assert gap.total_seconds() / 60 == layover["duration"]


def test_offsets_follow_the_travel_date(provider):
    """Synthetic: the TP 210 flight moved to July, when New York and Lisbon are on summer time."""
    option = copy.deepcopy(next(o for o in options(load("one_way")) if flight_numbers(o) == ("TP 210",)))
    flight, = option["flights"]
    flight["departure_airport"]["date"] = "2027-07-15"
    flight["arrival_airport"]["date"] = "2027-07-16"
    segment, = provider.map_leg(option, LINK)["segments"]
    assert segment["departure"] == "2027-07-15T22:00:00-04:00"
    assert segment["arrival"] == "2027-07-16T10:00:00+01:00"


def connecting_option():
    return copy.deepcopy(next(o for o in options(load("one_way")) if len(o["flights"]) == 2))


UNUSABLE = {
    "missing departure time": lambda o: o["flights"][0]["departure_airport"].pop("time"),
    "malformed time": lambda o: o["flights"][0]["departure_airport"].update(time="25:99"),
    "malformed date": lambda o: o["flights"][0]["arrival_airport"].update(date="2027-13-45"),
    "airport unknown to airportsdata": lambda o: o["flights"][1]["arrival_airport"].update(id="ZZZ"),
    "missing airport id": lambda o: o["flights"][0]["departure_airport"].pop("id"),
    "segments do not connect": lambda o: o["flights"][1]["departure_airport"].update(id="MAD"),
    "missing segment duration": lambda o: o["flights"][0].pop("duration"),
    "missing total duration": lambda o: o.pop("total_duration"),
    "no segments": lambda o: o.update(flights=[]),
}


@pytest.mark.parametrize("break_it", UNUSABLE.values(), ids=UNUSABLE.keys())
def test_unusable_option_is_rejected_not_guessed(provider, break_it):
    """Synthetic: the real connecting option (JFK, DUB, LIS) with one thing broken."""
    option = connecting_option()
    break_it(option)
    with pytest.raises(provider.UnusableOption):
        provider.map_leg(option, LINK)


# --- Group 1: prices ---------------------------------------------------------------------

async def test_one_adult_price_is_the_per_person_price(provider, api, query):
    result = await provider.fetch_flight_candidates(**query)
    assert {numbers(c): c["price_per_person"] for c in result} == {
        flight_numbers(o): o["price"] for o in options(load("one_way"))}


async def test_two_adult_price_is_a_party_total_and_is_split(provider, api, query):
    """Recorded: every 2-adult price is 2x the 1-adult price, so `price` is the party total."""
    result = await provider.fetch_flight_candidates(**{**query, "adults": 2})
    expected = {flight_numbers(o): o["price"] / 2 for o in options(load("one_way_2_adults"))}
    assert {numbers(c): c["price_per_person"] for c in result} == pytest.approx(expected)


@pytest.mark.parametrize("price", [MISSING, "N/A", 0, -5, True, None],
                         ids=["missing", "text", "zero", "negative", "boolean", "null"])
async def test_option_with_an_unusable_price_is_dropped(provider, api, query, price):
    """Synthetic: the real response with one option's price broken."""
    body = load("one_way")
    broken = options(body)[0]
    if price is MISSING:
        del broken["price"]
    else:
        broken["price"] = price
    api.respond = lambda request: ok(body)
    result = await provider.fetch_flight_candidates(**query)
    assert len(result) == 4
    assert flight_numbers(broken) not in {numbers(c) for c in result}


async def test_all_options_unusable_is_a_data_error_not_no_flights(provider, api, query):
    body = load("one_way")
    for option in options(body):
        del option["price"]
    api.respond = lambda request: ok(body)
    with pytest.raises(ToolError, match="UPSTREAM_DATA_ERROR"):
        await provider.fetch_flight_candidates(**query)


# --- Group 3: round trips ----------------------------------------------------------------

async def test_round_trip_pairs_the_outbound_with_each_return_option(provider, api, round_trip_query):
    outbound = first_outbound()
    returns = options(load("round_trip_return"))
    result = await provider.fetch_flight_candidates(**round_trip_query)
    assert len(result) == len(returns) == 3
    by_return = {numbers(c, 1): c for c in result}
    for option in returns:
        candidate = by_return[flight_numbers(option)]
        assert numbers(candidate, 0) == flight_numbers(outbound)
        assert len(candidate["legs"]) == 2
        out_leg, back_leg = candidate["legs"]
        assert (out_leg["origin"], out_leg["destination"]) == ("JFK", "LIS")
        assert (back_leg["origin"], back_leg["destination"]) == ("LIS", "JFK")
        # The whole-trip price comes from the return response, not the outbound's 704.
        assert candidate["price_per_person"] == option["price"]
        assert candidate["total_travel_minutes"] == outbound["total_duration"] + option["total_duration"]
        assert candidate["total_stops"] == (len(outbound["flights"]) - 1) + (len(option["flights"]) - 1)
        link = load("round_trip_return")["search_metadata"]["request_url"]
        assert candidate["google_flights_url"] == link
    assert by_return[("DL 273",)]["price_per_person"] == 704
    assert by_return[("KL 1580", "KL 643")]["price_per_person"] == 715


async def test_ids_are_stable_unique_and_not_ranking_positions(provider, api, round_trip_query):
    first = await provider.fetch_flight_candidates(**round_trip_query)
    second = await provider.fetch_flight_candidates(**round_trip_query)
    assert [c["id"] for c in first] == [c["id"] for c in second]
    assert len({c["id"] for c in first}) == len(first)
    assert not any(c["id"].isdigit() for c in first)


async def test_outbounds_without_return_options_yield_no_candidates(provider, api, round_trip_query):
    def respond(request):
        if "departure_token" in request.url.params:
            return ok(no_options(load("round_trip_return")))
        return ok(load("round_trip_outbound"))
    api.respond = respond
    result = await provider.fetch_flight_candidates(**round_trip_query)
    assert result == [] and result.exhaustive is True
    assert len(api.requests) == 1 + len(options(load("round_trip_outbound")))


async def test_a_follow_up_that_does_not_return_home_is_not_a_round_trip(provider, api, round_trip_query):
    outbound = load("round_trip_outbound")
    api.respond = lambda request: ok(outbound)  # follow-ups also answer with outbound-only flights
    with pytest.raises(ToolError, match="UPSTREAM_DATA_ERROR"):
        await provider.fetch_flight_candidates(**round_trip_query)


async def test_a_return_that_departs_before_the_outbound_lands_is_not_a_round_trip(
        provider, api, round_trip_query):
    """Synthetic: the real return response with every date moved 8 days earlier, so the
    return flights leave before the outbound (2027-01-22) arrives."""
    returns = load("round_trip_return")
    for option in options(returns):
        for flight in option["flights"]:
            for point in (flight["departure_airport"], flight["arrival_airport"]):
                point["date"] = (date.fromisoformat(point["date"]) - timedelta(days=8)).isoformat()
    original = fixture_respond
    api.respond = lambda request: (ok(returns) if "departure_token" in request.url.params
                                   else original(request))
    with pytest.raises(ToolError, match="UPSTREAM_DATA_ERROR"):
        await provider.fetch_flight_candidates(**round_trip_query)


async def test_a_failed_follow_up_is_a_failure_not_no_availability(provider, api, round_trip_query):
    def respond(request):
        if "departure_token" in request.url.params:
            return httpx.Response(503, json={})
        return ok(load("round_trip_outbound"))
    api.respond = respond
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await provider.fetch_flight_candidates(**round_trip_query)
    assert caught.value.response.status_code == 503


# --- Group 4: empty and malformed responses -----------------------------------------------

@pytest.mark.parametrize("body", [no_options(load("one_way")),
                                  {**no_options(load("one_way")), "best_flights": [], "other_flights": []}],
                         ids=["no-keys", "empty-lists"])
async def test_a_response_without_options_is_an_empty_success(provider, api, query, body):
    """Synthetic: what SearchApi returns when no flights exist is not recorded yet."""
    api.respond = lambda request: ok(body)
    result = await provider.fetch_flight_candidates(**query)
    assert result == [] and result.exhaustive is True and result.requests_used == 1


@pytest.mark.parametrize("respond", [
    lambda request: httpx.Response(200, text="<html>not json</html>"),
    lambda request: ok([]),
    lambda request: ok({"error": "synthetic: shape of a real error response is unrecorded"}),
], ids=["not-json", "not-an-object", "error-key"])
async def test_uninterpretable_response_is_a_data_error(provider, api, query, respond):
    api.respond = respond
    with pytest.raises(ToolError, match="UPSTREAM_DATA_ERROR"):
        await provider.fetch_flight_candidates(**query)


# --- Group 5: request building -------------------------------------------------------------

async def test_one_way_request_parameters(provider, api, query):
    await provider.fetch_flight_candidates(**query)
    request, = api.requests
    assert (request.url.host, request.url.path) == ("www.searchapi.io", "/api/v1/search")
    # `stops` is deliberately not sent: the tool applies max_stops itself.
    assert dict(request.url.params) == {
        "engine": "google_flights", "departure_id": "JFK", "arrival_id": "LIS",
        "flight_type": "one_way", "outbound_date": "2027-01-21", "travel_class": "economy",
        "adults": "1", "children": "0", "infants_in_seat": "0", "infants_on_lap": "0",
        "currency": "USD"}


@pytest.mark.parametrize("cabin,sent", [("economy", "economy"), ("premium_economy", "premium_economy"),
                                        ("business", "business"), ("first", "first_class")])
async def test_cabin_is_mapped_to_the_provider_travel_class(provider, api, query, cabin, sent):
    await provider.fetch_flight_candidates(**{**query, "cabin": cabin})
    assert api.requests[0].url.params["travel_class"] == sent


async def test_airport_sets_travel_in_one_request(provider, api, query):
    await provider.fetch_flight_candidates(
        **{**query, "origin": ["JFK", "EWR", "LGA"], "destination": ["LIS", "OPO"]})
    request, = api.requests
    assert request.url.params["departure_id"] == "JFK,EWR,LGA"
    assert request.url.params["arrival_id"] == "LIS,OPO"


async def test_every_passenger_count_is_sent(provider, api, query):
    api.respond = lambda request: ok(no_options(load("one_way")))
    await provider.fetch_flight_candidates(
        **{**query, "adults": 2, "children": 1, "infants_in_seat": 1, "infants_on_lap": 1, "currency": "EUR"})
    params = api.requests[0].url.params
    assert (params["adults"], params["children"], params["infants_in_seat"],
            params["infants_on_lap"], params["currency"]) == ("2", "1", "1", "1", "EUR")


async def test_round_trip_search_and_follow_up_requests(provider, api, round_trip_query):
    await provider.fetch_flight_candidates(**round_trip_query)
    first, *follow_ups = api.requests
    base = dict(first.url.params)
    assert base["flight_type"] == "round_trip" and base["return_date"] == "2027-01-28"
    assert "departure_token" not in base
    expected_tokens = [o["departure_token"] for o in options(load("round_trip_outbound"))]
    assert [r.url.params["departure_token"] for r in follow_ups] == expected_tokens
    for request in follow_ups:
        params = dict(request.url.params)
        params.pop("departure_token")
        assert params == base


async def test_key_travels_in_the_authorization_header_only(provider, api, query):
    await provider.fetch_flight_candidates(**query)
    request, = api.requests
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url) and "api_key" not in request.url.params


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
async def test_missing_key_is_a_configuration_error_before_any_request(provider, api, query, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("SEARCHAPI_API_KEY", value)
    with pytest.raises(ToolError, match="CONFIGURATION_ERROR"):
        await provider.fetch_flight_candidates(**query)
    assert api.requests == []


# --- Group 6: spend limits -----------------------------------------------------------------

def test_limits_match_the_approved_values(provider):
    assert provider.MAX_REQUESTS_PER_CALL == 10
    assert provider.MAX_RETRIES == 1
    assert provider.REQUEST_TIMEOUT_SECONDS == 20
    assert 0 < provider.RETRY_DELAY_SECONDS <= 5


async def test_one_way_costs_exactly_one_request(provider, api, query):
    result = await provider.fetch_flight_candidates(**query)
    assert isinstance(result, list)
    assert len(api.requests) == result.requests_used == 1 and result.exhaustive is True


async def test_round_trip_costs_one_request_plus_one_per_outbound(provider, api, round_trip_query):
    result = await provider.fetch_flight_candidates(**round_trip_query)
    assert len(api.requests) == result.requests_used == 1 + len(options(load("round_trip_outbound")))
    assert result.exhaustive is True


async def test_follow_ups_stop_at_the_request_cap(provider, api, round_trip_query):
    api.respond = respond_many(12)
    result = await provider.fetch_flight_candidates(**round_trip_query)
    assert len(api.requests) == result.requests_used == provider.MAX_REQUESTS_PER_CALL
    assert result.exhaustive is False and len(result) > 0


async def test_a_retry_counts_toward_the_cap(provider, api, round_trip_query):
    failed = []

    def respond(request):
        if "departure_token" not in request.url.params:
            return ok(many_outbound(12))
        if not failed:
            failed.append(request)
            return httpx.Response(503, json={})
        return ok(load("round_trip_return"))
    api.respond = respond
    result = await provider.fetch_flight_candidates(**round_trip_query)
    assert len(api.requests) == result.requests_used == provider.MAX_REQUESTS_PER_CALL
    assert result.exhaustive is False


async def test_a_5xx_is_retried_once_after_a_delay(provider, api, query):
    replies = iter([httpx.Response(503, json={}), ok(load("one_way"))])
    api.respond = lambda request: next(replies)
    result = await provider.fetch_flight_candidates(**query)
    assert len(result) == 5 and len(api.requests) == result.requests_used == 2
    assert api.sleeps == [provider.RETRY_DELAY_SECONDS]


async def test_a_5xx_that_keeps_failing_is_raised_after_the_retry(provider, api, query):
    api.respond = status(503)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await provider.fetch_flight_candidates(**query)
    assert caught.value.response.status_code == 503
    assert len(api.requests) == 1 + provider.MAX_RETRIES


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429])
async def test_client_errors_are_never_retried(provider, api, query, code):
    api.respond = status(code)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await provider.fetch_flight_candidates(**query)
    assert caught.value.response.status_code == code
    assert len(api.requests) == 1 and api.sleeps == []


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("timed out"), httpx.ConnectError("refused")],
                         ids=["timeout", "connect-error"])
async def test_timeouts_and_connection_failures_are_not_retried(provider, api, query, failure):
    api.respond = raising(failure)
    with pytest.raises(type(failure)):
        await provider.fetch_flight_candidates(**query)
    assert len(api.requests) == 1 and api.sleeps == []


async def test_every_request_has_a_finite_timeout(provider, api, round_trip_query):
    await provider.fetch_flight_candidates(**round_trip_query)
    assert len(api.requests) > 1
    for request in api.requests:
        timeout = request.extensions["timeout"]
        assert set(timeout) == {"connect", "read", "write", "pool"}
        assert all(v is not None and 0 < v <= provider.REQUEST_TIMEOUT_SECONDS for v in timeout.values())


# --- Group 8: end to end through the MCP tool ------------------------------------------------

E2E_QUERY = {"origin": ["JFK"], "destination": ["LIS"], "depart_date": "2027-01-21", "cabin": "economy"}
E2E_ROUND_TRIP = {**E2E_QUERY, "return_date": "2027-01-28"}


@pytest.fixture
def server(provider):
    return importlib.import_module("fastflights_mcp.server")


@pytest.fixture
def frozen_today(monkeypatch, server):
    """The recordings are for 2027-01-21; keep the tool's 'today or later' check satisfied."""
    module = importlib.import_module("fastflights_mcp.tools.search_flights")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 23, 12, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(module, "datetime", FixedDatetime)


async def call(server, arguments):
    async with Client(server.mcp) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "search_flights" in names
        return await client.call_tool("search_flights", arguments)


def message(result):
    return " ".join(getattr(item, "text", "") for item in result.content)


async def test_one_way_search_end_to_end(server, api, frozen_today):
    result = await call(server, E2E_QUERY)
    assert not result.is_error
    data = result.structured_content
    assert len(api.requests) == 1
    assert len(data["results"]) == 5
    assert data["recommended_id"] in {x["id"] for x in data["results"]}
    assert data["cache_hit"] is False
    for option in data["results"]:
        for leg in option["legs"]:
            assert datetime.fromisoformat(leg["departure"]).utcoffset() is not None
    tap = next(x for x in data["results"] if x["legs"][0]["segments"][0]["flight_number"] == "TP 210")
    assert tap["legs"][0]["departure"] == "2027-01-21T22:00:00-05:00"
    assert tap["price_per_person"] == 404


async def test_round_trip_search_end_to_end(server, api, frozen_today):
    result = await call(server, E2E_ROUND_TRIP)
    assert not result.is_error
    data = result.structured_content
    assert len(data["results"]) == 3
    assert len(api.requests) == 1 + len(options(load("round_trip_outbound")))
    assert all(len(x["legs"]) == 2 for x in data["results"])
    delta = next(x for x in data["results"] if x["legs"][1]["segments"][0]["flight_number"] == "DL 273")
    assert delta["price_per_person"] == 704
    assert (delta["legs"][1]["origin"], delta["legs"][1]["destination"]) == ("LIS", "JFK")


async def test_nothing_is_printed_to_stdout(server, api, frozen_today, capsys):
    await call(server, E2E_ROUND_TRIP)
    assert capsys.readouterr().out == ""


E2E_FAILURES = [
    pytest.param(status(401, load("error_401_invalid_key")), "UPSTREAM_AUTH_ERROR", 1, id="401"),
    pytest.param(status(429), "RATE_LIMITED", 1, id="429"),
    pytest.param(status(503), "UPSTREAM_UNAVAILABLE", 2, id="503-after-one-retry"),
    pytest.param(raising(httpx.ReadTimeout("timed out")), "UPSTREAM_TIMEOUT", 1, id="timeout"),
    pytest.param(raising(httpx.ConnectError("refused")), "UPSTREAM_UNAVAILABLE", 1, id="connect-error"),
    pytest.param(lambda request: httpx.Response(200, text="<html>"), "UPSTREAM_DATA_ERROR", 1, id="not-json"),
]


@pytest.mark.parametrize("respond,category,attempts", E2E_FAILURES)
async def test_failures_reach_the_host_with_stable_categories(server, api, frozen_today, respond, category, attempts):
    api.respond = respond
    result = await call(server, E2E_QUERY)
    assert result.is_error
    assert category in message(result)
    assert len(api.requests) == attempts


async def test_provider_text_and_key_never_reach_the_host(server, api, frozen_today, caplog):
    caplog.set_level(logging.DEBUG)
    marker = "provider-says-secret-marker"
    api.respond = status(500, {"error": f"{marker} {KEY}"})
    result = await call(server, E2E_QUERY)
    assert result.is_error
    visible = message(result) + caplog.text
    for forbidden in (KEY, marker, "api_key", "Bearer"):
        assert forbidden not in visible


async def test_missing_key_reaches_the_host_as_a_configuration_error(server, api, frozen_today, monkeypatch):
    monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
    result = await call(server, E2E_QUERY)
    assert result.is_error and "CONFIGURATION_ERROR" in message(result)
    assert api.requests == []


async def test_a_failed_follow_up_reaches_the_host_as_an_error(server, api, frozen_today):
    def respond(request):
        if "departure_token" in request.url.params:
            return httpx.Response(503, json={})
        return ok(load("round_trip_outbound"))
    api.respond = respond
    result = await call(server, E2E_ROUND_TRIP)
    assert result.is_error and "UPSTREAM_UNAVAILABLE" in message(result)


async def test_hitting_the_request_cap_is_reported_as_not_exhaustive(provider, server, api, frozen_today):
    api.respond = respond_many(12)
    result = await call(server, E2E_ROUND_TRIP)
    assert not result.is_error
    assert len(api.requests) == provider.MAX_REQUESTS_PER_CALL
    data = result.structured_content
    assert data["results"]
    assert "exhaustive" in (data["message"] or "").lower()
