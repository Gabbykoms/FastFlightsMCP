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

Multi-city (the last section of this file)
------------------------------------------
fastflights_mcp.provider
  MULTI_CITY_BEAM = 3          most partial trips continued at each step
  async fetch_multi_city_candidates(**query) -> Candidates
      query: legs (2 to 6 dicts: origin, destination, depart_date, max_stops where
      max_stops is the leg's effective limit, int or None), cabin, adults, children,
      infants_in_seat, infants_on_lap, currency. Same key, errors and request cap as
      fetch_flight_candidates.
      One request per leg: flight_type=multi_city, multi_city_json = the legs as
      {departure_id, arrival_id, outbound_date}, and from the second request on the
      departure_token of the partial trip being continued. SearchApi answers with the
      options for that one leg, so a complete candidate is assembled from the chosen
      flights of every step, and its price is the last step's `price` (a whole-trip
      total) divided by adults.
      Beam: the partial trips are ranked by (price, travel minutes so far, id) and the
      best min(MULTI_CITY_BEAM, (MAX_REQUESTS_PER_CALL - 1) // (legs - 1)) are continued.
      Partial trips that break a rule are dropped before they are ranked: a leg outside
      its airport sets or over its max_stops, a leg that departs before the previous leg
      arrives (elapsed time, not clock readings), or a departure from a different airport
      than the previous arrival less than 24 elapsed hours later.
      `exhaustive` is False whenever a partial trip was pruned by the beam or the cap.
      A body whose only content is the error "Google Flights didn't return any results."
      is a dead branch: skipped, counted as a request, never an error by itself. When every
      branch is dead the result is empty. Any other `error` body is UPSTREAM_DATA_ERROR.
fastflights_mcp.server.fetch_multi_city_candidates delegates to the provider.

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
from zoneinfo import ZoneInfo

import airportsdata
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


# --- Group 6: multi-city -------------------------------------------------------------------

DEAD_BRANCH = "Google Flights didn't return any results."
AIRPORTS = airportsdata.load("IATA")
NYC, LONDON, PARIS = ["JFK", "EWR", "LGA"], ["LHR", "LGW"], ["CDG", "ORY"]


@pytest.fixture
def mc_query():
    def leg(origin, destination, depart_date):
        return {"origin": origin, "destination": destination,
                "depart_date": depart_date, "max_stops": None}
    return {"legs": [leg(NYC, LONDON, "2027-01-05"), leg(LONDON, PARIS, "2027-01-09"),
                     leg(PARIS, NYC, "2027-01-14")],
            "cabin": "economy", "adults": 1, "children": 0, "infants_in_seat": 0,
            "infants_on_lap": 0, "currency": "USD"}


def by_number(response, number):
    return next(o for o in options(response) if number in flight_numbers(o))


def trimmed(response, *keep):
    """Synthetic: a recorded response cut down to the options that have a recorded follow-up."""
    return {**response, "best_flights": list(keep), "other_flights": []}


def recorded_chain(request):
    """Serve the recorded 3-leg chain. Only some options have a recorded follow-up, so the
    recorded responses for leg 1 and leg 2 are cut down to those options (synthetic):
    leg 1 keeps UA 122 (EWR-LHR); leg 2 keeps U2 8405 (LGW-CDG, a dead branch in the
    recording) and VY 8058 (LHR-ORY, which has recorded leg-3 options)."""
    token = request.url.params.get("departure_token")
    leg1, leg2 = load("multi_city_leg1"), load("multi_city_leg2")
    ua122 = by_number(leg1, "UA 122")
    dead, live = by_number(leg2, "U2 8405"), by_number(leg2, "VY 8058")
    if token is None:
        return ok(trimmed(leg1, ua122))
    if token == ua122["departure_token"]:
        return ok(trimmed(leg2, dead, live))
    if token == live["departure_token"]:
        return ok(load("multi_city_leg3"))
    if token == dead["departure_token"]:
        return ok(load("multi_city_leg3_no_results"))
    raise AssertionError("a token the recordings do not cover was requested")


def synthetic_option(origin, destination, departs, minutes=400, price=500, stops=0, number="SY100"):
    """Synthetic SearchApi option; `departs` is an aware UTC datetime, local times are derived."""
    def point(code, moment):
        local = moment.astimezone(ZoneInfo(AIRPORTS[code]["tz"]))
        return {"name": code, "id": code, "date": local.date().isoformat(),
                "time": local.strftime("%H:%M")}
    hubs = ["KEF"] * stops
    codes = [origin, *hubs, destination]
    flying = (minutes - 60 * stops) // (stops + 1)
    flights, start = [], departs
    for i in range(stops + 1):
        end = start + timedelta(minutes=flying)
        flights.append({"departure_airport": point(codes[i], start),
                        "arrival_airport": point(codes[i + 1], end), "duration": flying,
                        "airline": "Synth Air", "flight_number": f"{number[:-1]}{i}"})
        start = end + timedelta(minutes=60)
    return {"flights": flights, "total_duration": minutes, "price": price, "type": "Multi city"}


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def synthetic_chain(levels):
    """Synthetic SearchApi: `levels[k]` is the list of options answering step k. Every option
    but the last step's gets a departure_token "t<k>.<index>:<parent>", unique per parent;
    the last step's options get a booking_token."""
    def respond(request):
        token = request.url.params.get("departure_token")
        step = 0 if token is None else int(token[1:token.index(".")]) + 1
        shown = []
        for i, option in enumerate(levels[step]):
            option = copy.deepcopy(option)
            if step < len(levels) - 1:
                option["departure_token"] = f"t{step}.{i}:{token or ''}"
            else:
                option["booking_token"] = f"b{i}"
            shown.append(option)
        return ok({"search_metadata": {}, "best_flights": [], "other_flights": shown})
    return respond


ROUTE = ["JFK", "LHR", "CDG", "FCO", "MAD", "LIS", "JFK"]


def flat_levels(count, per_step=5):
    """Synthetic: `count` steps along ROUTE, `per_step` options each, option i priced 500 + 10 i."""
    return [[synthetic_option(ROUTE[k], ROUTE[k + 1], utc("2027-01-05T09:00") + timedelta(days=3 * k),
                              minutes=480, price=500 + 10 * i, number=f"S{k}{i:02d}0")
             for i in range(per_step)] for k in range(count)]


def flat_query(mc_query, count):
    legs = [{"origin": [ROUTE[k]], "destination": [ROUTE[k + 1]],
             "depart_date": f"2027-01-{5 + 3 * k:02d}", "max_stops": None} for k in range(count)]
    return {**mc_query, "legs": legs}


def step_tokens(api, step):
    """Departure tokens sent to get step `step` (2-based: step 1 needs none), in order. A token
    carries the step of the response that issued it, so step 2 is asked with "t0." tokens."""
    return [r.url.params["departure_token"] for r in api.requests
            if r.url.params.get("departure_token", "").startswith(f"t{step - 2}.")]


async def test_multi_city_request_parameters(provider, api, mc_query):
    api.respond = recorded_chain
    await provider.fetch_multi_city_candidates(**mc_query)
    first = api.requests[0]
    assert (first.url.host, first.url.path) == ("www.searchapi.io", "/api/v1/search")
    params = dict(first.url.params)
    legs = json.loads(params.pop("multi_city_json"))
    assert legs == [{"departure_id": "JFK,EWR,LGA", "arrival_id": "LHR,LGW", "outbound_date": "2027-01-05"},
                    {"departure_id": "LHR,LGW", "arrival_id": "CDG,ORY", "outbound_date": "2027-01-09"},
                    {"departure_id": "CDG,ORY", "arrival_id": "JFK,EWR,LGA", "outbound_date": "2027-01-14"}]
    assert params == {"engine": "google_flights", "flight_type": "multi_city",
                      "travel_class": "economy", "adults": "1", "children": "0",
                      "infants_in_seat": "0", "infants_on_lap": "0", "currency": "USD"}
    assert KEY not in str(first.url)
    assert first.headers["Authorization"] == f"Bearer {KEY}"


async def test_multi_city_every_passenger_count_and_cabin_are_sent(provider, api, mc_query):
    api.respond = lambda request: ok(load("multi_city_leg3_no_results"))
    await provider.fetch_multi_city_candidates(
        **{**mc_query, "cabin": "first", "adults": 2, "children": 1, "infants_in_seat": 1,
           "infants_on_lap": 1, "currency": "EUR"})
    params = api.requests[0].url.params
    assert (params["travel_class"], params["adults"], params["children"],
            params["infants_in_seat"], params["infants_on_lap"], params["currency"]) == (
        "first_class", "2", "1", "1", "1", "EUR")


async def test_recorded_chain_follows_the_tokens_one_step_at_a_time(provider, api, mc_query):
    api.respond = recorded_chain
    leg1, leg2 = load("multi_city_leg1"), load("multi_city_leg2")
    ua122 = by_number(leg1, "UA 122")["departure_token"]
    live, dead = (by_number(leg2, n)["departure_token"] for n in ("VY 8058", "U2 8405"))
    await provider.fetch_multi_city_candidates(**mc_query)
    first, second, *last = api.requests
    assert "departure_token" not in first.url.params
    assert second.url.params["departure_token"] == ua122
    assert sorted(r.url.params["departure_token"] for r in last) == sorted([live, dead])
    # every follow-up repeats the same legs
    assert {r.url.params["multi_city_json"] for r in api.requests} == {first.url.params["multi_city_json"]}


async def test_recorded_chain_builds_complete_itineraries(provider, api, mc_query):
    api.respond = recorded_chain
    result = await provider.fetch_multi_city_candidates(**mc_query)
    final = options(load("multi_city_leg3"))
    assert len(result) == len(final) == 16
    assert len({c["id"] for c in result}) == 16 and all(c["id"].startswith("fl_") for c in result)
    for candidate in result:
        assert set(candidate) == {"id", "legs", "price_per_person", "currency",
                                  "total_travel_minutes", "total_stops",
                                  "google_flights_url", "separate_tickets"}
        assert candidate["separate_tickets"] is None and candidate["currency"] == "USD"
        assert len(candidate["legs"]) == 3
        assert numbers(candidate, 0) == ("UA 122",) and numbers(candidate, 1) == ("VY 8058",)
        assert candidate["total_travel_minutes"] == sum(l["duration_minutes"] for l in candidate["legs"])
        assert candidate["total_stops"] == sum(l["stops"] for l in candidate["legs"])
        assert all(l["google_flights_url"].startswith("https://") for l in candidate["legs"])
        # the legs chain in time, with real UTC offsets
        for earlier, later in zip(candidate["legs"], candidate["legs"][1:]):
            assert datetime.fromisoformat(later["departure"]) >= datetime.fromisoformat(earlier["arrival"])
    by_last = {numbers(c, 2): c for c in result}
    for option in final:
        # the last step's price is the whole-trip total
        assert by_last[flight_numbers(option)]["price_per_person"] == option["price"]
    assert result.exhaustive is True and result.requests_used == len(api.requests) == 4


async def test_dead_branch_in_the_recording_is_skipped_not_fatal(provider, api, mc_query):
    """The recording's U2 8405 follow-up returns HTTP 200 with `error`; its sibling works."""
    api.respond = recorded_chain
    result = await provider.fetch_multi_city_candidates(**mc_query)
    assert len(result) == 16 and not any(numbers(c, 1) == ("U2 8405",) for c in result)


async def test_final_price_is_the_last_step_price_per_person(provider, api, mc_query):
    query = flat_query(mc_query, 2)
    levels = flat_levels(2, per_step=1)
    levels[1][0]["price"] = 912
    api.respond = synthetic_chain(levels)
    result = await provider.fetch_multi_city_candidates(**{**query, "adults": 2})
    candidate, = result
    assert candidate["price_per_person"] == 456


async def test_children_and_infants_are_not_priced(provider, api, mc_query):
    api.respond = synthetic_chain(flat_levels(2, per_step=1))
    with pytest.raises(ToolError, match="UNSUPPORTED_FILTER"):
        await provider.fetch_multi_city_candidates(**{**flat_query(mc_query, 2), "children": 1})


async def test_dead_branch_text_is_the_only_error_that_is_skipped(provider, api, mc_query):
    api.respond = lambda request: ok({"search_metadata": {}, "error": "Invalid API key."})
    with pytest.raises(ToolError, match="UPSTREAM_DATA_ERROR"):
        await provider.fetch_multi_city_candidates(**mc_query)


async def test_a_dead_first_request_is_an_empty_result(provider, api, mc_query):
    api.respond = lambda request: ok(load("multi_city_leg3_no_results"))
    result = await provider.fetch_multi_city_candidates(**mc_query)
    assert result == [] and result.exhaustive is True and result.requests_used == 1


async def test_every_branch_dead_is_an_empty_result(provider, api, mc_query):
    levels = flat_levels(2, per_step=3)

    def respond(request):
        if "departure_token" in request.url.params:
            return ok(load("multi_city_leg3_no_results"))
        return synthetic_chain(levels)(request)

    api.respond = respond
    result = await provider.fetch_multi_city_candidates(**flat_query(mc_query, 2))
    assert result == [] and result.exhaustive is True and result.requests_used == 4


@pytest.mark.parametrize("legs,expected_requests", [(2, 4), (3, 7), (4, 10), (5, 9), (6, 6)])
async def test_beam_width_keeps_every_search_within_the_request_cap(
        provider, api, mc_query, legs, expected_requests):
    """Beam 3 where it fits: 1 + 3 x (legs - 1) requests, narrower once that exceeds 10."""
    api.respond = synthetic_chain(flat_levels(legs))
    result = await provider.fetch_multi_city_candidates(**flat_query(mc_query, legs))
    assert len(api.requests) == result.requests_used == expected_requests
    assert expected_requests <= provider.MAX_REQUESTS_PER_CALL
    assert result.exhaustive is False and len(result) > 0
    assert all(len(c["legs"]) == legs for c in result)


async def test_beam_continues_the_cheapest_partial_trips(provider, api, mc_query):
    api.respond = synthetic_chain(flat_levels(3, per_step=6))
    result = await provider.fetch_multi_city_candidates(**flat_query(mc_query, 3))
    assert sorted(t.split(":")[0] for t in step_tokens(api, 2)) == ["t0.0", "t0.1", "t0.2"]
    # ties on price across parents: the three kept partials all end with the cheapest option
    assert len(step_tokens(api, 3)) == 3 and all(t.startswith("t1.0:") for t in step_tokens(api, 3))
    assert len(result) == 3 * 6


async def test_equal_prices_are_ranked_by_travel_time(provider, api, mc_query):
    levels = [[synthetic_option("JFK", "LHR", utc("2027-01-05T09:00"), minutes=m, price=500,
                                number=f"S0{i}0")
               for i, m in enumerate([400, 300, 350, 450])],
              [synthetic_option("LHR", "CDG", utc("2027-01-09T09:00"), number="S100")]]
    api.respond = synthetic_chain(levels)
    await provider.fetch_multi_city_candidates(**flat_query(mc_query, 2))
    kept = sorted(t.split(":")[0] for t in step_tokens(api, 2))
    assert kept == ["t0.0", "t0.1", "t0.2"]  # the 450-minute option (index 3) is the one pruned


async def test_pruning_is_reported_and_a_complete_search_is_not(provider, api, mc_query):
    query = flat_query(mc_query, 2)
    api.respond = synthetic_chain(flat_levels(2, per_step=3))
    assert (await provider.fetch_multi_city_candidates(**query)).exhaustive is True
    api.respond = synthetic_chain(flat_levels(2, per_step=4))
    assert (await provider.fetch_multi_city_candidates(**query)).exhaustive is False


async def test_retries_count_toward_the_request_cap(provider, api, mc_query):
    inner = synthetic_chain(flat_levels(4))
    failed = []

    def respond(request):
        if step_tokens_of(request) == 2 and not failed:
            failed.append(request)
            return httpx.Response(503, json={"error": "synthetic"})
        return inner(request)

    def step_tokens_of(request):
        token = request.url.params.get("departure_token", "")
        return int(token[1:token.index(".")]) + 1 if token.startswith("t") else 0

    api.respond = respond
    result = await provider.fetch_multi_city_candidates(**flat_query(mc_query, 4))
    assert failed and len(api.requests) == result.requests_used == provider.MAX_REQUESTS_PER_CALL
    assert result.exhaustive is False and len(api.sleeps) == 1


async def test_partial_trips_that_depart_before_the_previous_arrival_do_not_use_a_beam_slot(
        provider, api, mc_query):
    """Step 2 options 0-2 are the cheapest but leave before any step 1 option lands."""
    early = [synthetic_option("LHR", "CDG", utc("2027-01-05T10:00"), price=400 + 10 * i,
                              number=f"S1{i:02d}0") for i in range(3)]
    later = [synthetic_option("LHR", "CDG", utc("2027-01-09T09:00"), price=400 + 10 * i,
                              number=f"S1{i:02d}0") for i in range(3, 9)]
    levels = [flat_levels(1, per_step=6)[0], early + later,
              [synthetic_option("CDG", "FCO", utc("2027-01-12T09:00"), number="S200")]]
    query = flat_query(mc_query, 3)
    query["legs"][1].update(origin=["LHR"], destination=["CDG"])
    query["legs"][0].update(origin=["JFK"], destination=["LHR"])
    query["legs"][2].update(origin=["CDG"], destination=["FCO"])
    api.respond = synthetic_chain(levels)
    await provider.fetch_multi_city_candidates(**query)
    assert len(step_tokens(api, 3)) == 3
    assert all(int(t[3:t.index(":")]) >= 3 for t in step_tokens(api, 3))  # index of a later option


def two_leg_query(mc_query, destination=("LHR", "LGW"), origin=("LHR", "LGW")):
    legs = [{"origin": ["JFK"], "destination": list(destination), "depart_date": "2027-01-05",
             "max_stops": None},
            {"origin": list(origin), "destination": ["CDG"], "depart_date": "2027-01-06",
             "max_stops": None}]
    return {**mc_query, "legs": legs}


def kept_second_legs(result):
    return sorted(numbers(c, 1) for c in result)


@pytest.mark.parametrize("second_departure,second_origin,kept", [
    ("2027-01-06T15:59", "LHR", False),   # 23h59m after landing at LGW: an airport change is too soon
    ("2027-01-06T16:00", "LHR", True),    # exactly 24 hours: allowed
    ("2027-01-05T20:00", "LGW", True),    # same airport, 4 hours later: allowed
    ("2027-01-05T15:00", "LGW", False),   # leaves before the first leg lands
])
async def test_airport_change_needs_twenty_four_elapsed_hours(
        provider, api, mc_query, second_departure, second_origin, kept):
    levels = [[synthetic_option("JFK", "LGW", utc("2027-01-05T09:00"), minutes=420, number="S000")],
              [synthetic_option(second_origin, "CDG", utc(second_departure), number="S100")]]
    api.respond = synthetic_chain(levels)
    result = await provider.fetch_multi_city_candidates(**two_leg_query(mc_query))
    assert (len(result) == 1) is kept


@pytest.mark.parametrize("second_departure,kept", [
    ("2027-03-28T11:00", False),  # 12:00 BST the next day: 24 clock hours, only 23 elapsed
    ("2027-03-28T12:00", True),   # 13:00 BST: 24 elapsed hours
])
async def test_the_twenty_four_hours_are_elapsed_time_across_a_clock_change(
        provider, api, mc_query, second_departure, kept):
    """London moves to summer time on 2027-03-28; local clocks alone would get this wrong."""
    levels = [[synthetic_option("JFK", "LHR", utc("2027-03-27T05:00"), minutes=420, number="S000")],
              [synthetic_option("LGW", "CDG", utc(second_departure), number="S100")]]
    api.respond = synthetic_chain(levels)
    result = await provider.fetch_multi_city_candidates(**two_leg_query(mc_query))
    assert (len(result) == 1) is kept


async def test_legs_outside_the_requested_airports_are_dropped(provider, api, mc_query):
    levels = [[synthetic_option("JFK", "LHR", utc("2027-01-05T09:00"), number="S000"),
               synthetic_option("BOS", "LHR", utc("2027-01-05T09:00"), number="S010"),
               synthetic_option("JFK", "STN", utc("2027-01-05T09:00"), number="S020")],
              [synthetic_option("LHR", "CDG", utc("2027-01-09T09:00"), number="S100"),
               synthetic_option("LHR", "ORY", utc("2027-01-09T09:00"), number="S110")]]
    api.respond = synthetic_chain(levels)
    query = two_leg_query(mc_query, destination=("LHR",), origin=("LHR",))
    result = await provider.fetch_multi_city_candidates(**query)
    assert [numbers(c, 0) for c in result] == [("S000",)] * 1 and kept_second_legs(result) == [("S100",)]


@pytest.mark.parametrize("first_limit,second_limit,expected", [
    (None, None, {("S000", "S100"), ("S000", "S110"), ("S010", "S100"), ("S010", "S110")}),
    (0, None, {("S000", "S100"), ("S000", "S110")}),
    (None, 0, {("S000", "S100"), ("S010", "S100")}),
    (0, 1, {("S000", "S100"), ("S000", "S110")}),
    (1, 0, {("S000", "S100"), ("S010", "S100")}),
])
async def test_each_leg_is_held_to_its_own_stop_limit(
        provider, api, mc_query, first_limit, second_limit, expected):
    levels = [[synthetic_option("JFK", "LHR", utc("2027-01-05T09:00"), number="S000"),
               synthetic_option("JFK", "LHR", utc("2027-01-05T10:00"), stops=1, number="S010")],
              [synthetic_option("LHR", "CDG", utc("2027-01-09T09:00"), number="S100"),
               synthetic_option("LHR", "CDG", utc("2027-01-09T10:00"), stops=1, number="S110")]]
    api.respond = synthetic_chain(levels)
    query = two_leg_query(mc_query, destination=("LHR",), origin=("LHR",))
    query["legs"][0]["max_stops"], query["legs"][1]["max_stops"] = first_limit, second_limit
    result = await provider.fetch_multi_city_candidates(**query)
    got = {(numbers(c, 0)[0], numbers(c, 1)[0]) for c in result}
    # the one-stop options have a second segment "S0x1"; compare on the first segment only
    assert got == expected


async def test_unusable_options_are_dropped_and_all_unusable_is_a_data_error(provider, api, mc_query):
    good = synthetic_option("LHR", "CDG", utc("2027-01-09T09:00"), number="S100")
    broken = {"flights": [], "total_duration": 90, "price": 400}
    api.respond = synthetic_chain([[synthetic_option("JFK", "LHR", utc("2027-01-05T09:00"),
                                                     number="S000")], [broken, good]])
    result = await provider.fetch_multi_city_candidates(
        **two_leg_query(mc_query, destination=("LHR",), origin=("LHR",)))
    assert len(result) == 1 and numbers(result[0], 1) == ("S100",)
    api.respond = synthetic_chain([[broken], [good]])
    with pytest.raises(ToolError, match="UPSTREAM_DATA_ERROR"):
        await provider.fetch_multi_city_candidates(
            **two_leg_query(mc_query, destination=("LHR",), origin=("LHR",)))


async def test_multi_city_without_a_key_makes_no_request(provider, api, mc_query, monkeypatch):
    monkeypatch.delenv("SEARCHAPI_API_KEY")
    with pytest.raises(ToolError, match="CONFIGURATION_ERROR"):
        await provider.fetch_multi_city_candidates(**mc_query)
    assert api.requests == []


@pytest.mark.parametrize("code", [401, 429, 503])
async def test_multi_city_http_failures_propagate_for_the_tool_to_map(provider, api, mc_query, code):
    api.respond = status(code)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch_multi_city_candidates(**mc_query)


async def test_the_server_hook_delegates_to_the_provider(provider, server, api, mc_query):
    api.respond = recorded_chain
    result = await server.fetch_multi_city_candidates(**mc_query)
    assert len(result) == 16
