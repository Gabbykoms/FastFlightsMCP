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
import logging
import os
from datetime import datetime
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
TRAVEL_CLASS = {"economy": "economy", "premium_economy": "premium_economy",
                "business": "business", "first": "first_class"}

_TRANSPORT: httpx.AsyncBaseTransport | None = None  # test hook: replaces the network


async def _sleep(seconds: float) -> None:  # test hook: lets tests skip the retry delay
    await asyncio.sleep(seconds)


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
                   usage: _Usage) -> dict[str, Any]:
    """One logical request: retries 5xx once; every attempt counts toward the cap."""
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
