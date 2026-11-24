"""search_multi_city: validate a trip of 2 to 6 legs, then filter, rank and select complete trips.

Ranking, the 3+2 budget allocation, the inclusive 20% ceiling and the model types are the
ones search_flights uses, applied to the whole trip. What is specific here is the route:
each leg must start in the city the previous leg ended in, and changing airports within a
city needs a full day in between.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fastflights_mcp.places import same_city
from fastflights_mcp.provider import AIRPORT_CHANGE_MIN_GAP
from fastflights_mcp.tools.search_flights import (
    MAX_PARTY, AirportSet, DateString, FlightSearchResult, Itinerary, _aware, _parse_date,
    _reasons, _select, _upstream_error)

logger = logging.getLogger(__name__)


class LegRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: AirportSet
    destination: AirportSet
    depart_date: DateString
    max_stops: Annotated[int | None, Field(strict=True, ge=0, le=2,
                                           description="Stops allowed on this leg only; "
                                                       "overrides the top-level max_stops.")] = None


def _validate(legs: list[LegRequest], adults, children, infants_in_seat, infants_on_lap,
              budget, strict_budget) -> None:
    """Cross-field checks that must finish before any upstream request."""
    for i, leg in enumerate(legs):
        for name, airports in (("origin", leg.origin), ("destination", leg.destination)):
            if len(set(airports)) != len(airports):
                raise ToolError(f"legs[{i}].{name} must not repeat an airport code.")
        if all(same_city(o, d) for o in leg.origin for d in leg.destination):
            raise ToolError(f"legs[{i}] starts and ends in the same city; a leg must go "
                            "somewhere else.")
    dates = [_parse_date(f"legs[{i}].depart_date", leg.depart_date) for i, leg in enumerate(legs)]
    if dates[0] < datetime.now(timezone.utc).date():
        raise ToolError("legs[0].depart_date must be today or later (UTC).")
    for i in range(1, len(legs)):
        if dates[i] < dates[i - 1]:
            raise ToolError(f"legs[{i}].depart_date is before legs[{i - 1}].depart_date; "
                            "legs must be in travel order.")
        for a in legs[i - 1].destination:
            for b in legs[i].origin:
                if not same_city(a, b):
                    raise ToolError(
                        f"legs[{i}] must start in the city legs[{i - 1}] ends in: {a} and {b} "
                        "are in different cities. Ask the traveler to revise the route; do "
                        "not add a leg.")
    if infants_on_lap > adults:
        raise ToolError("infants_on_lap cannot exceed adults.")
    if adults + children + infants_in_seat + infants_on_lap > MAX_PARTY:
        raise ToolError(f"The whole party cannot exceed {MAX_PARTY} passengers.")
    if strict_budget and budget is None:
        raise ToolError("strict_budget requires a budget.")
    if children or infants_in_seat or infants_on_lap:
        # Same stopgap as search_flights: one party total, no derivable per-person price.
        raise ToolError("UNSUPPORTED_FILTER: Searches with children or infants are not "
                        "supported yet because the provider returns one price for the whole "
                        "party. Search with adults only.")


def _normalize(raw: dict[str, Any], legs: list[LegRequest], currency: str) -> Itinerary | None:
    """Return a structurally valid complete trip with recomputed totals, or None."""
    try:
        option = Itinerary.model_validate(raw)
        if len(option.legs) != len(legs):
            return None
        for flown, wanted in zip(option.legs, legs):
            if flown.origin not in wanted.origin or flown.destination not in wanted.destination:
                return None
        for earlier, later in zip(option.legs, option.legs[1:]):
            if _aware(later.departure) < _aware(earlier.arrival):
                return None
        if option.currency != currency or option.price_per_person <= 0:
            return None
        minutes = sum(leg.duration_minutes for leg in option.legs)
        if minutes <= 0:
            return None
        # Time spent visiting a city between legs is neither travel time nor a stop.
        return option.model_copy(update={"total_travel_minutes": minutes,
                                         "total_stops": sum(leg.stops for leg in option.legs)})
    except (ValidationError, ValueError, TypeError):
        return None


def _airport_changes(option: Itinerary) -> list[tuple[int, str, str]]:
    return [(i + 1, earlier.destination, later.origin)
            for i, (earlier, later) in enumerate(zip(option.legs, option.legs[1:]))
            if earlier.destination != later.origin]


def _allowed(option: Itinerary, limits: list[int | None]) -> bool:
    """The rules a valid trip must still meet: per-leg stops and the 24-hour airport change."""
    if any(limit is not None and leg.stops > limit for leg, limit in zip(option.legs, limits)):
        return False
    for earlier, later in zip(option.legs, option.legs[1:]):
        if earlier.destination != later.origin and (
                _aware(later.departure) - _aware(earlier.arrival) < AIRPORT_CHANGE_MIN_GAP):
            return False
    return True


async def search_multi_city(
    legs: Annotated[list[LegRequest], Field(min_length=2, max_length=6,
                                            description="The trip's legs in travel order, 2 to 6.")],
    cabin: Literal["economy", "premium_economy", "business", "first"],
    adults: Annotated[int, Field(strict=True, ge=1, le=9)] = 1,
    children: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
    infants_in_seat: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
    infants_on_lap: Annotated[int, Field(strict=True, ge=0, le=8)] = 0,
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD",
    budget: Annotated[float | None, Field(strict=True, gt=0, allow_inf_nan=False,
                                          description="Per person, whole trip.")] = None,
    strict_budget: Annotated[bool, Field(strict=True)] = False,
    max_stops: Annotated[int | None, Field(strict=True, ge=0, le=2,
                                           description="Per leg, unless a leg sets its own.")] = None,
) -> FlightSearchResult:
    """Find complete multi-city trips (2 to 6 legs) once every leg's places, date and the cabin are known.

    Call this after city names are resolved to IATA airport codes (use search_airports for
    every leg). Each leg must start in the city the previous leg ended in; if the traveler's
    route has a gap, ask them to revise it instead of adding a leg. Returns up to five
    complete trips, each with every leg, and one recommendation. An empty result is a normal
    answer; ask the traveler before changing the budget, dates or airports. Prices and
    budgets are per person for the whole trip. Children and infants are not supported yet.
    """
    _validate(legs, adults, children, infants_in_seat, infants_on_lap, budget, strict_budget)
    limits = [leg.max_stops if leg.max_stops is not None else max_stops for leg in legs]
    search = {"legs": [{**leg.model_dump(), "max_stops": limit} for leg, limit in zip(legs, limits)],
              "cabin": cabin, "adults": adults, "children": children,
              "infants_in_seat": infants_in_seat, "infants_on_lap": infants_on_lap,
              "currency": currency}
    # Looked up at call time so the adapter can be replaced (tests, later the real client).
    from fastflights_mcp import server
    try:
        candidates = await server.fetch_multi_city_candidates(**search)
    except ToolError:
        raise
    except Exception as exc:
        raise _upstream_error(exc) from None

    usable = [o for raw in candidates if (o := _normalize(raw, legs, currency))]
    if candidates and not usable:
        raise ToolError("UPSTREAM_DATA_ERROR: The provider returned results that could not "
                        "be validated as complete trips.")
    usable = [o for o in usable if _allowed(o, limits)]

    selected, recommended, within_found = _select(usable, budget, strict_budget)
    message = None
    if recommended is not None:
        recommended = recommended.model_copy(
            update={"recommendation_reasons": _reasons(recommended, budget, currency)})
        selected = [recommended if o.id == recommended.id else o for o in selected]
        if within_found is False:
            message = ("No trip fits the budget; showing alternatives within the 20% allowance.")
    else:
        message = ("No eligible complete itinerary was found for these legs, dates and "
                   "filters. Ask the traveler whether to change the budget, dates or airports.")
    changes = sorted({(a, b) for o in selected for _, a, b in _airport_changes(o)})
    if changes:
        note = ("Airport change: " + "; ".join(f"arrive {a}, depart {b}" for a, b in changes)
                + ". The traveler arranges the transfer between these airports.")
        message = note if message is None else f"{message} {note}"
    if not getattr(candidates, "exhaustive", True):
        note = ("Not exhaustive: the upstream request limit and search width stopped the search "
                "early, so better trips may exist.")
        message = note if message is None else f"{message} {note}"
    return FlightSearchResult(
        query={**search, "budget": budget, "strict_budget": strict_budget, "max_stops": max_stops},
        fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        cache_hit=False, message=message, results=selected,
        recommended_id=recommended.id if recommended else None,
        within_budget_found=within_found)


def register(app: MCPServer) -> None:
    app.add_tool(search_multi_city)
    # Same closing as search_flights.register: the SDK's argument model ignores unknown keys.
    arguments = app._tool_manager.get_tool("search_multi_city").fn_metadata.arg_model
    arguments.model_config["extra"] = "forbid"
    arguments.model_rebuild(force=True)
