"""SearchApi adapter: sends bounded requests and maps answers to normalized candidates.

The search_flights tool calls `fetch_flight_candidates` (through `server`) and never sees
SearchApi's format. Everything provider-specific lives here.

Maintenance guide
-----------------
* Limits are the constants below. Tests pin them; change both together. Every request,
  retries and round-trip follow-ups included, counts toward MAX_REQUESTS_PER_CALL, and each
  one is billed.
* SearchApi answers a round trip in two steps: the first response holds outbound legs only,
  and each outbound option's `departure_token` needs its own follow-up request to get the
  return legs and the whole-trip `price`.
* `price` is the party total, not a per-person fare (recorded in
  tests/fixtures/searchapi/README.md). Adults-only parties divide it by the number of
  adults. A child's fare can differ, so parties with children or infants are refused here
  and in the tool.
* Times arrive as local date plus HH:MM with no offset. `airportsdata` maps an IATA code to
  an IANA zone and `zoneinfo` supplies the offset for that date. An airport it does not
  know makes the option unusable; an offset is never guessed.
* Multi-city is a chain of one request per leg. Each response lists the options for that one
  leg, and an option's `departure_token` selects it for the next request; the last leg's
  options carry the whole-trip `price`. Trying every combination would cost far more than
  MAX_REQUESTS_PER_CALL, so each stage expands the best partial trips (ranked by projected
  price, then travel time) until MULTI_CITY_BEAM of them are live, and the result says when
  anything was left out. A token can lead nowhere: SearchApi then answers HTTP 200 with the
  `error` "Google Flights didn't return any results." (recorded in
  tests/fixtures/searchapi/multi_city_leg3_no_results.json). Such a dead branch, and one whose
  request times out, is replaced by the next-ranked partial trip while the spare request budget
  allows; the spare never takes requests a later stage needs. Any other `error` body is still
  UPSTREAM_DATA_ERROR and other HTTP errors still end the search.
* Multi-city requests use their own timeout (MULTI_CITY_REQUEST_TIMEOUT_SECONDS) because chained
  requests were measured at up to 28.7 s, and the whole call stops starting requests after
  MULTI_CITY_DEADLINE_SECONDS. Both numbers come from 15 timed requests; re-measure before
  tightening them.
* HTTP and transport failures propagate as httpx exceptions; the tool maps them to
  UPSTREAM_* categories. This module raises ToolError only for CONFIGURATION_ERROR,
  UPSTREAM_DATA_ERROR and UNSUPPORTED_FILTER, and never puts provider text or the key in
  a message or log line.
* To re-record a fixture, make one request by hand with the key in an Authorization
  header, redact the search IDs in `search_metadata`, scan for the key, and update the
  fixtures README. Re-run the tests afterwards.
"""
import asyncio
import functools
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import airportsdata
import httpx
from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger(__name__)

SEARCHAPI_URL = "https://www.searchapi.io/api/v1/search"
GENERIC_FLIGHTS_URL = "https://www.google.com/travel/flights"
MAX_REQUESTS_PER_CALL = 10
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 20.0
MULTI_CITY_BEAM = 3
MULTI_CITY_REQUEST_TIMEOUT_SECONDS = 45.0  # chained requests were measured at up to 28.7 s
MULTI_CITY_DEADLINE_SECONDS = 120.0
AIRPORT_CHANGE_MIN_GAP = timedelta(hours=24)
DEAD_BRANCH_ERROR = "Google Flights didn't return any results."
TRAVEL_CLASS = {"economy": "economy", "premium_economy": "premium_economy",
                "business": "business", "first": "first_class"}

_TRANSPORT: httpx.AsyncBaseTransport | None = None  # test hook: replaces the network


async def _sleep(seconds: float) -> None:  # test hook: lets tests skip the retry delay
    await asyncio.sleep(seconds)


def _clock() -> float:  # test hook: lets tests move the multi-city deadline
    return time.monotonic()


class UnusableOption(ValueError):
    """A provider option that cannot be validated; it is dropped, never repaired."""


class Candidates(list):
    """Normalized itineraries plus whether the search finished and what it cost."""

    def __init__(self, items=(), *, exhaustive: bool = True, requests_used: int = 0):
        super().__init__(items)
        self.exhaustive = exhaustive
        self.requests_used = requests_used


class _CapReached(Exception):
    """The per-call request allowance is spent; no further request may be sent."""


class _DeadlineReached(Exception):
    """The multi-city call has used its time allowance; no further request may start."""


class _Usage:
    def __init__(self) -> None:
        self.used = 0


@functools.lru_cache(maxsize=1)
def _airports() -> dict[str, dict[str, Any]]:
    return airportsdata.load("IATA")


def _is_minutes(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _timestamp(point: Any) -> datetime:
    """Local date and HH:MM at an airport to an aware datetime."""
    try:
        zone = ZoneInfo(_airports()[point["id"]]["tz"])
        moment = datetime.fromisoformat(f"{point['date']}T{point['time']}")
    except (KeyError, ValueError, TypeError) as exc:
        raise UnusableOption("airport, date or time cannot be validated") from exc
    if moment.tzinfo is not None:
        raise UnusableOption("provider time already carries an offset")
    return moment.replace(tzinfo=zone)


def _segment(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UnusableOption("segment is not an object")
    departure = _timestamp(raw.get("departure_airport"))
    arrival = _timestamp(raw.get("arrival_airport"))
    if arrival <= departure:
        raise UnusableOption("segment does not arrive after it departs")
    airline, number = raw.get("airline"), raw.get("flight_number")
    if not isinstance(airline, str) or not airline:
        raise UnusableOption("segment has no airline")
    if number is not None and not isinstance(number, str):
        raise UnusableOption("flight number is not text")
    if not _is_minutes(raw.get("duration")):
        raise UnusableOption("segment has no usable duration")
    return {"airline": airline, "flight_number": number or None,
            "origin": raw["departure_airport"]["id"], "destination": raw["arrival_airport"]["id"],
            "departure": departure.isoformat(), "arrival": arrival.isoformat(),
            "duration_minutes": raw["duration"]}


def map_leg(option: Any, google_flights_url: str) -> dict[str, Any]:
    """One SearchApi option to one leg; raises UnusableOption when it cannot be validated."""
    if not isinstance(option, dict):
        raise UnusableOption("option is not an object")
    flights, total = option.get("flights"), option.get("total_duration")
    if not isinstance(flights, list) or not flights:
        raise UnusableOption("option has no segments")
    if not _is_minutes(total):
        raise UnusableOption("option has no usable total duration")
    segments = [_segment(raw) for raw in flights]
    for earlier, later in zip(segments, segments[1:]):
        if earlier["destination"] != later["origin"]:
            raise UnusableOption("segments do not connect")
    return {"origin": segments[0]["origin"], "destination": segments[-1]["destination"],
            "departure": segments[0]["departure"], "arrival": segments[-1]["arrival"],
            "duration_minutes": total, "stops": len(segments) - 1, "segments": segments,
            "google_flights_url": google_flights_url}


def _price(option: dict[str, Any]) -> float:
    price = option.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
        raise UnusableOption("option has no usable price")
    return price


def _per_person(total: float, query: dict[str, Any]) -> float:
    if query["children"] or query["infants_in_seat"] or query["infants_on_lap"]:
        raise ToolError("UNSUPPORTED_FILTER: Searches with children or infants are not "
                        "supported yet; search with adults only.")
    return total / query["adults"]


def _identify(legs: list[dict[str, Any]]) -> str:
    """Stable ID of the selected flight combination, not of its provider rank."""
    parts = [f"{s['airline']}|{s['flight_number']}|{s['origin']}|{s['departure']}"
             for leg in legs for s in leg["segments"]]
    return "fl_" + hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]


def _candidate(legs: list[dict[str, Any]], total: float, query: dict[str, Any],
               link: str) -> dict[str, Any]:
    return {"id": _identify(legs), "legs": legs, "price_per_person": _per_person(total, query),
            "currency": query["currency"],
            "total_travel_minutes": sum(leg["duration_minutes"] for leg in legs),
            "total_stops": sum(leg["stops"] for leg in legs),
            "google_flights_url": link, "separate_tickets": None}


def _params(query: dict[str, Any], departure_token: str | None = None) -> dict[str, str]:
    params = {"engine": "google_flights", "departure_id": ",".join(query["origin"]),
              "arrival_id": ",".join(query["destination"]),
              "flight_type": "round_trip" if query.get("return_date") else "one_way",
              "outbound_date": query["depart_date"], "travel_class": TRAVEL_CLASS[query["cabin"]],
              "adults": str(query["adults"]), "children": str(query["children"]),
              "infants_in_seat": str(query["infants_in_seat"]),
              "infants_on_lap": str(query["infants_on_lap"]), "currency": query["currency"]}
    if query.get("return_date"):
        params["return_date"] = query["return_date"]
    if departure_token:
        params["departure_token"] = departure_token
    return params


def _data_error(reason: str) -> ToolError:
    return ToolError(f"UPSTREAM_DATA_ERROR: {reason}")


async def _request(client: httpx.AsyncClient, key: str, params: dict[str, str],
                   usage: _Usage, dead_branch_ok: bool = False) -> dict[str, Any]:
    """One logical request: retries 5xx once; every attempt counts toward the cap.

    With `dead_branch_ok`, SearchApi's "no results" error body is returned as it is (it has
    no options) instead of raising, so a multi-city search can skip that branch.
    """
    attempt = 0
    while True:
        if usage.used >= MAX_REQUESTS_PER_CALL:
            raise _CapReached
        usage.used += 1
        try:
            response = await client.get(SEARCHAPI_URL, params=params,
                                        headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < MAX_RETRIES:
                attempt += 1
                await _sleep(RETRY_DELAY_SECONDS)
                continue
            raise
        try:
            body = response.json()
        except ValueError:
            raise _data_error("The provider response was not valid JSON.") from None
        if isinstance(body, dict) and dead_branch_ok and body.get("error") == DEAD_BRANCH_ERROR:
            return body
        if not isinstance(body, dict) or "error" in body:
            raise _data_error("The provider response could not be interpreted.")
        return body


def _options(body: dict[str, Any]) -> list[Any]:
    found: list[Any] = []
    for name in ("best_flights", "other_flights"):
        group = body.get(name) or []
        if not isinstance(group, list):
            raise _data_error("The provider response could not be interpreted.")
        found += group
    return found


def _link(body: dict[str, Any]) -> str:
    metadata = body.get("search_metadata")
    link = metadata.get("request_url") if isinstance(metadata, dict) else None
    return link if isinstance(link, str) and link.startswith("https://") else GENERIC_FLIGHTS_URL


def _within(leg: dict[str, Any], origin: list[str], destination: list[str]) -> None:
    if leg["origin"] not in origin or leg["destination"] not in destination:
        raise UnusableOption("leg airports are outside the requested sets")


async def fetch_flight_candidates(**query: Any) -> Candidates:
    """Search SearchApi and return normalized, complete one-way or round-trip candidates."""
    key = (os.environ.get("SEARCHAPI_API_KEY") or "").strip()
    if not key:
        raise ToolError("CONFIGURATION_ERROR: SEARCHAPI_API_KEY is not set on the server. "
                        "The operator must configure it.")
    usage, exhaustive, unusable = _Usage(), True, 0
    candidates: list[dict[str, Any]] = []
    round_trip = bool(query.get("return_date"))
    async with httpx.AsyncClient(transport=_TRANSPORT,
                                 timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS)) as client:
        first = await _request(client, key, _params(query), usage)
        link = _link(first)
        for option in _options(first):
            try:
                if not round_trip:
                    leg = map_leg(option, link)
                    _within(leg, query["origin"], query["destination"])
                    candidates.append(_candidate([leg], _price(option), query, link))
                    continue
                token = option.get("departure_token") if isinstance(option, dict) else None
                if not isinstance(token, str) or not token:
                    raise UnusableOption("outbound option cannot be completed")
                outbound = map_leg(option, "")
                _within(outbound, query["origin"], query["destination"])
            except UnusableOption as exc:
                unusable += 1
                logger.warning("Dropped an unusable SearchApi option: %s", exc)
                continue
            try:
                answer = await _request(client, key, _params(query, token), usage)
            except _CapReached:
                exhaustive = False
                logger.warning("Request limit reached; the search is not exhaustive.")
                break
            back_link = _link(answer)
            for ret in _options(answer):
                try:
                    back = map_leg(ret, back_link)
                    _within(back, query["destination"], query["origin"])
                    if datetime.fromisoformat(back["departure"]) < datetime.fromisoformat(
                            outbound["arrival"]):
                        raise UnusableOption("return departs before the outbound arrives")
                    legs = [{**outbound, "google_flights_url": back_link}, back]
                    candidates.append(_candidate(legs, _price(ret), query, back_link))
                except UnusableOption as exc:
                    unusable += 1
                    logger.warning("Dropped an unusable SearchApi option: %s", exc)
    if unusable and not candidates:
        raise _data_error("The provider returned results that could not be validated.")
    return Candidates(candidates, exhaustive=exhaustive, requests_used=usage.used)


def _multi_city_params(query: dict[str, Any], departure_token: str | None = None) -> dict[str, str]:
    legs = [{"departure_id": ",".join(leg["origin"]), "arrival_id": ",".join(leg["destination"]),
             "outbound_date": leg["depart_date"]} for leg in query["legs"]]
    params = {"engine": "google_flights", "flight_type": "multi_city",
              "multi_city_json": json.dumps(legs), "travel_class": TRAVEL_CLASS[query["cabin"]],
              "adults": str(query["adults"]), "children": str(query["children"]),
              "infants_in_seat": str(query["infants_in_seat"]),
              "infants_on_lap": str(query["infants_on_lap"]), "currency": query["currency"]}
    if departure_token:
        params["departure_token"] = departure_token
    return params


def _follows(previous: dict[str, Any], leg: dict[str, Any]) -> bool:
    """Can `leg` come after `previous`? Elapsed time, not clock readings: the two timestamps
    carry their own UTC offsets. A different departure airport needs a full day in between."""
    landed, departs = datetime.fromisoformat(previous["arrival"]), datetime.fromisoformat(
        leg["departure"])
    if departs < landed:
        return False
    return leg["origin"] == previous["destination"] or departs - landed >= AIRPORT_CHANGE_MIN_GAP


async def fetch_multi_city_candidates(**query: Any) -> Candidates:
    """Search a chain of 2 to 6 legs and return normalized, complete multi-city candidates.

    Partial trips are expanded best first. A branch is *live* when its response gives at least
    one usable continuation (at the last step, at least one candidate). A stage expands branches
    until `width` are live; a dead branch (no results, nothing usable, or a timeout) is replaced
    by the next-ranked partial trip while the spare request budget allows. The spare never takes
    requests that a later stage needs for its own `width` branches.
    """
    key = (os.environ.get("SEARCHAPI_API_KEY") or "").strip()
    if not key:
        raise ToolError("CONFIGURATION_ERROR: SEARCHAPI_API_KEY is not set on the server. "
                        "The operator must configure it.")
    requested, usage = query["legs"], _Usage()
    last = len(requested) - 1
    width = min(MULTI_CITY_BEAM, (MAX_REQUESTS_PER_CALL - 1) // last)
    exhaustive, unusable, timed_out = True, 0, None
    candidates: list[dict[str, Any]] = []
    ranked: list[dict[str, Any] | None] = [None]  # partial trips to expand; None = nothing chosen yet
    started = _clock()
    timeout = httpx.Timeout(MULTI_CITY_REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(transport=_TRANSPORT, timeout=timeout) as client:
        try:
            for step, spec in enumerate(requested):
                needed = 1 if step == 0 else width
                reserved = width * (last - step)  # requests the later stages need for themselves
                partials: list[dict[str, Any]] = []
                live = expanded = 0
                for parent in ranked:
                    if live >= needed:
                        break
                    if expanded >= needed and usage.used + 1 > MAX_REQUESTS_PER_CALL - reserved:
                        break  # a refill would starve a later stage
                    if _clock() - started >= MULTI_CITY_DEADLINE_SECONDS:
                        raise _DeadlineReached
                    expanded += 1
                    try:
                        body = await _request(client, key, _multi_city_params(
                            query, parent["token"] if parent else None), usage, dead_branch_ok=True)
                    except httpx.TimeoutException as exc:
                        timed_out, exhaustive = exc, False  # a timed-out branch may hide trips
                        logger.warning("A multi-city request timed out (%s); skipping that branch.",
                                       type(exc).__name__)
                        continue
                    link, branch = _link(body), 0
                    for option in _options(body):
                        try:
                            leg = map_leg(option, link)
                            _within(leg, spec["origin"], spec["destination"])
                            price = _price(option)
                            next_token = option.get("departure_token") if isinstance(option, dict) else None
                            if step < last and not (isinstance(next_token, str) and next_token):
                                raise UnusableOption("option cannot be continued")
                        except UnusableOption as exc:
                            unusable += 1
                            logger.warning("Dropped an unusable SearchApi option: %s", exc)
                            continue
                        limit = spec.get("max_stops")
                        if (limit is not None and leg["stops"] > limit) or (
                                parent and not _follows(parent["legs"][-1], leg)):
                            continue
                        legs = (parent["legs"] if parent else []) + [leg]
                        branch += 1
                        if step == last:
                            candidates.append(_candidate(legs, price, query, link))
                        else:
                            partials.append({"legs": legs, "token": next_token, "price": price,
                                             "minutes": sum(x["duration_minutes"] for x in legs),
                                             "id": _identify(legs)})
                    live += bool(branch)
                if expanded < len(ranked):  # a ranked partial trip was never expanded
                    exhaustive = False
                    logger.warning("Multi-city search expanded %d of %d partial trips.",
                                   expanded, len(ranked))
                if step == last:
                    break
                partials.sort(key=lambda p: (p["price"], p["minutes"], p["id"]))
                ranked = partials
                if not ranked:
                    break
        except _CapReached:
            exhaustive = False
            logger.warning("Request limit reached; the search is not exhaustive.")
        except _DeadlineReached:
            exhaustive = False
            logger.warning("Deadline reached; the search is not exhaustive.")
    if timed_out is not None and not candidates:
        raise timed_out
    if unusable and not candidates:
        raise _data_error("The provider returned results that could not be validated.")
    return Candidates(candidates, exhaustive=exhaustive, requests_used=usage.used)
