"""search_flights: validate a request, then filter, rank and select complete itineraries."""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import cmp_to_key
from typing import Annotated, Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
WITHIN_TARGET = 3
OVER_TARGET = MAX_RESULTS - WITHIN_TARGET
OVER_ALLOWANCE = Decimal("1.2")  # inclusive 20% ceiling
SIMILAR_MINUTES = 60
MAX_PARTY = 9

AirportSet = Annotated[
    list[Annotated[str, Field(pattern=r"^[A-Z]{3}$")]],
    Field(min_length=1, max_length=10,
          description="Distinct uppercase IATA airport codes, 1 to 10."),
]
DateString = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class Segment(BaseModel):
    airline: str
    flight_number: str | None
    origin: str
    destination: str
    departure: str
    arrival: str
    duration_minutes: int


class FlightLeg(BaseModel):
    origin: str
    destination: str
    departure: str
    arrival: str
    duration_minutes: int
    stops: int
    segments: list[Segment]
    google_flights_url: str


class Itinerary(BaseModel):
    id: str
    legs: list[FlightLeg]
    price_per_person: float
    currency: str
    total_travel_minutes: int
    total_stops: int
    google_flights_url: str
    separate_tickets: bool | None = None
    budget_status: Literal["not_specified", "within_budget", "over_budget"] = "not_specified"
    budget_overage: float | None = None
    recommendation_reasons: list[str] = []


class FlightSearchResult(BaseModel):
    query: dict[str, Any]
    fetched_at: str
    cache_hit: bool
    message: str | None
    results: list[Itinerary]
    recommended_id: str | None
    within_budget_found: bool | None


def _parse_date(name: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{name} must be a valid calendar date in YYYY-MM-DD format.") from None


def _validate(origin, destination, depart_date, return_date, adults, children,
              infants_in_seat, infants_on_lap, budget, strict_budget):
    """Cross-field checks that must finish before any upstream request."""
    for name, airports in (("origin", origin), ("destination", destination)):
        if len(set(airports)) != len(airports):
            raise ToolError(f"{name} must not repeat an airport code.")
    departure = _parse_date("depart_date", depart_date)
    if departure < datetime.now(timezone.utc).date():
        raise ToolError("depart_date must be today or later (UTC).")
    if return_date is not None and _parse_date("return_date", return_date) < departure:
        raise ToolError("return_date must be on or after depart_date.")
    if infants_on_lap > adults:
        raise ToolError("infants_on_lap cannot exceed adults.")
    if adults + children + infants_in_seat + infants_on_lap > MAX_PARTY:
        raise ToolError(f"The whole party cannot exceed {MAX_PARTY} passengers.")
    if strict_budget and budget is None:
        raise ToolError("strict_budget requires a budget.")
    if children or infants_in_seat or infants_on_lap:
        # SearchApi returns one party total and a child's fare can differ from an adult's,
        # so no per-person price can be derived yet (see spec.md section 3.3).
        raise ToolError("UNSUPPORTED_FILTER: Searches with children or infants are not "
                        "supported yet because the provider returns one price for the whole "
                        "party. Search with adults only.")


def _aware(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() is None:
        raise ValueError("timestamp without UTC offset")
    return moment


def _normalize(raw: dict[str, Any], origin: list[str], destination: list[str],
               round_trip: bool, currency: str) -> Itinerary | None:
    """Return a validated complete itinerary, or None when the candidate is unusable."""
    try:
        option = Itinerary.model_validate(raw)
        legs = option.legs
        if len(legs) != (2 if round_trip else 1):
            return None
        if legs[0].origin not in origin or legs[0].destination not in destination:
            return None
        if round_trip:
            back = legs[1]
            if back.origin not in destination or back.destination not in origin:
                return None
            if _aware(back.departure) < _aware(legs[0].arrival):
                return None
        if option.currency != currency or option.price_per_person <= 0:
            return None
        if option.total_travel_minutes <= 0:
            return None
        return option
    except (ValidationError, ValueError, TypeError):
        return None


def _compare(a: Itinerary, b: Itinerary) -> int:
    """Fewer stops and shorter trips first; price decides between similar trips.

    Similar means the same number of stops and durations within one hour. The
    relation is not transitive, so the caller pre-sorts to keep results stable.
    """
    if (a.total_stops == b.total_stops
            and abs(a.total_travel_minutes - b.total_travel_minutes) <= SIMILAR_MINUTES):
        left = (a.price_per_person, a.total_travel_minutes, a.id)
        right = (b.price_per_person, b.total_travel_minutes, b.id)
    else:
        left = (a.total_travel_minutes, a.total_stops, a.price_per_person, a.id)
        right = (b.total_travel_minutes, b.total_stops, b.price_per_person, b.id)
    return (left > right) - (left < right)


def _rank(options: list[Itinerary]) -> list[Itinerary]:
    stable = sorted(options, key=lambda o: (o.total_travel_minutes, o.total_stops,
                                           o.price_per_person, o.id))
    return sorted(stable, key=cmp_to_key(_compare))


def _select(options: list[Itinerary], budget: float | None, strict: bool):
    """Return (selected, recommended, within_budget_found) under the agreed budget rules."""
    ranked = _rank(options)
    if budget is None:
        chosen = ranked[:MAX_RESULTS]
        return chosen, chosen[0] if chosen else None, None
    limit = Decimal(str(budget))
    within = [o for o in ranked if Decimal(str(o.price_per_person)) <= limit]
    over = [] if strict else [
        o for o in ranked
        if limit < Decimal(str(o.price_per_person)) <= limit * OVER_ALLOWANCE]
    if within:  # an alternative must beat the best within-budget option to be worthwhile
        over = [o for o in over if _compare(o, within[0]) < 0]
    take_over = min(len(over), max(OVER_TARGET, MAX_RESULTS - len(within)))
    take_within = min(len(within), MAX_RESULTS - take_over)
    selected = [o.model_copy(update={"budget_status": "within_budget", "budget_overage": 0.0})
                for o in within[:take_within]]
    selected += [
        o.model_copy(update={"budget_status": "over_budget",
                             "budget_overage": float(Decimal(str(o.price_per_person)) - limit)})
        for o in over[:take_over]]
    return selected, selected[0] if selected else None, bool(within)


def _reasons(option: Itinerary, budget: float | None, currency: str) -> list[str]:
    stops = "Nonstop" if option.total_stops == 0 else f"{option.total_stops} stop(s) in total"
    hours, minutes = divmod(option.total_travel_minutes, 60)
    reasons = [stops, f"Total travel time {hours}h {minutes:02d}m",
               f"{option.price_per_person:g} {currency} per person"]
    if option.budget_status == "within_budget":
        reasons.append(f"Within the {budget:g} {currency} per-person budget")
    elif option.budget_status == "over_budget":
        reasons.append(f"Over the budget by {option.budget_overage:g} {currency} per person; "
                       "no within-budget option was available")
    return reasons


def _upstream_error(exc: Exception) -> ToolError:
    """Map a provider failure to a stable, sanitized category; never echo its text."""
    logger.warning("SearchApi request failed: %s", type(exc).__name__)
    if isinstance(exc, httpx.TimeoutException):
        return ToolError("UPSTREAM_TIMEOUT: The flight provider timed out. Retry shortly "
                         "or narrow the request.")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return ToolError("UPSTREAM_AUTH_ERROR: The flight provider rejected the server's "
                             "credentials. The operator must check the API key.")
        if status == 429:
            wait = exc.response.headers.get("Retry-After", "")
            hint = f" Retry after {wait} seconds." if wait.isdigit() else ""
            return ToolError("RATE_LIMITED: The flight provider is rate limiting requests. "
                             f"Retry later.{hint}")
        if status >= 500:
            return ToolError("UPSTREAM_UNAVAILABLE: The flight provider is unavailable. "
                             "Retry later.")
        return ToolError("UPSTREAM_DATA_ERROR: The flight provider rejected the request.")
    if isinstance(exc, httpx.TransportError):
        return ToolError("UPSTREAM_UNAVAILABLE: The flight provider could not be reached. "
                         "Retry later.")
    return ToolError("UPSTREAM_DATA_ERROR: Unexpected failure while contacting the flight "
                     "provider; details are in the server log.")


async def search_flights(
    origin: AirportSet,
    destination: AirportSet,
    depart_date: DateString,
    cabin: Literal["economy", "premium_economy", "business", "first"],
    return_date: DateString | None = None,
    adults: Annotated[int, Field(strict=True, ge=1, le=9)] = 1,
    children: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
    infants_in_seat: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
    infants_on_lap: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD",
    budget: Annotated[float | None, Field(strict=True, gt=0, allow_inf_nan=False,
                                          description="Per person, whole trip.")] = None,
    strict_budget: Annotated[bool, Field(strict=True)] = False,
    max_stops: Annotated[int | None, Field(strict=True, ge=0, le=2,
                                           description="Per requested leg.")] = None,
) -> FlightSearchResult:
    """Find complete one-way or round-trip flights once locations, dates and cabin are known.

    Call this after city names are resolved to IATA airport codes (use search_airports).
    Omit return_date for one-way. Returns up to five itineraries and one recommendation.
    An empty result is a normal answer; ask the traveler before changing the budget,
    dates or airports. Prices and budgets are per person for the whole itinerary.
    Children and infants are not supported yet: the party must be adults only.
    """
    _validate(origin, destination, depart_date, return_date, adults, children,
              infants_in_seat, infants_on_lap, budget, strict_budget)
    search = {"origin": origin, "destination": destination, "depart_date": depart_date,
              "return_date": return_date, "cabin": cabin, "adults": adults,
              "children": children, "infants_in_seat": infants_in_seat,
              "infants_on_lap": infants_on_lap, "currency": currency}
    # Looked up at call time so the adapter can be replaced (tests, later the real client).
    from fastflights_mcp import server
    try:
        candidates = await server.fetch_flight_candidates(**search)
    except ToolError:
        raise
    except Exception as exc:
        raise _upstream_error(exc) from None

    usable = [o for raw in candidates
              if (o := _normalize(raw, origin, destination, return_date is not None, currency))]
    if candidates and not usable:
        raise ToolError("UPSTREAM_DATA_ERROR: The provider returned results that could not "
                        "be validated as complete itineraries.")
    if max_stops is not None:
        usable = [o for o in usable if all(leg.stops <= max_stops for leg in o.legs)]

    selected, recommended, within_found = _select(usable, budget, strict_budget)
    message = None
    if recommended is not None:
        recommended = recommended.model_copy(
            update={"recommendation_reasons": _reasons(recommended, budget, currency)})
        selected = [recommended if o.id == recommended.id else o for o in selected]
        if within_found is False:
            message = ("No itinerary fits the budget; showing alternatives within the "
                       "20% allowance.")
    else:
        message = ("No eligible complete itinerary was found for these airports, dates and "
                   "filters. Ask the traveler whether to change the budget, dates or airports.")
    if not getattr(candidates, "exhaustive", True):
        note = ("Not exhaustive: the upstream request limit stopped the search early, so "
                "better options may exist.")
        message = note if message is None else f"{message} {note}"
    return FlightSearchResult(
        query={**search, "budget": budget, "strict_budget": strict_budget,
               "max_stops": max_stops},
        fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        cache_hit=False, message=message, results=selected,
        recommended_id=recommended.id if recommended else None,
        within_budget_found=within_found)


def register(app: MCPServer) -> None:
    app.add_tool(search_flights)
    # The SDK's generated argument model ignores unknown keys, so a typo such as
    # `max_stop` would silently run an unfiltered search. Close the model instead.
    # This uses SDK internals (_tool_manager); the failing-test suite catches a break.
    arguments = app._tool_manager.get_tool("search_flights").fn_metadata.arg_model
    arguments.model_config["extra"] = "forbid"
    arguments.model_rebuild(force=True)
