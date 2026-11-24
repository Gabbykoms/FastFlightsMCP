"""search_airports: resolve a city or airport name to IATA codes.

Register on the shared `mcp` from `fastflights_mcp.server`. This module is
imported by the server's `main()` so registration happens at startup; do not
create a second MCP application here.

Two seams tests rely on:
    CACHE                 object with get(key)/set(key, value). Week 9 uses an
                          in-memory store; Redis replaces it later with the
                          same interface.
    _location_search(query) -> list[dict]
                          the only function that calls SearchApi. Tests
                          monkeypatch this to fake the upstream.
"""
import functools
import logging
import os
import re
from typing import Annotated, Optional

import airportsdata
import httpx
from pydantic import BaseModel, Field

from fastflights_mcp.server import mcp
from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger(__name__)

# Up to ten matches per spec section 3.2; matches are returned in upstream order.
_MAX_MATCHES = 10

# Provider request timeout. Per spec, finite per-request timeouts and bounded
# retries are required before depending on this in production.
_REQUEST_TIMEOUT = httpx.Timeout(10.0)


class AirportMatch(BaseModel):
    iata: str
    name: str
    city: str
    country: str


class AirportSearchResult(BaseModel):
    matches: list[AirportMatch]
    message: Optional[str] = None


class _InMemoryCache:
    """Process-local cache for Week 9. Redis replaces this later with the
    same get/set interface; tests inject a dict-backed fake instead.
    """

    def __init__(self) -> None:
        self._store: dict[str, AirportSearchResult] = {}

    def get(self, key: str) -> Optional[AirportSearchResult]:
        return self._store.get(key)

    def set(self, key: str, value: AirportSearchResult) -> None:
        self._store[key] = value


CACHE = _InMemoryCache()


def _normalize_key(query: str) -> str:
    """Trim, collapse whitespace, and case-fold so cached lookups survive
    cosmetic differences.
    """
    return re.sub(r"\s+", " ", query.strip()).casefold()


_IATA_CODE = re.compile(r"^[A-Z]{3}$")


@functools.lru_cache(maxsize=1)
def _airport_table() -> dict:
    return airportsdata.load("IATA")


def _to_matches(locations: list) -> tuple[list[AirportMatch], str]:
    """Airports of the best matching place, and that place's name.

    SearchApi returns places (mostly cities, best match first), each with a nested
    `airports` list; "New York" gives the city (JFK, LGA, EWR) and then four upstate
    cities. Only the first place with usable airports is used, so a model never searches
    Buffalo flights for a New York request. `country` is not in the response; it comes
    from airportsdata. Duplicate and malformed codes are skipped. A non-empty response
    with no readable airports is an upstream data error, never a blank success.
    """
    if not locations:
        return [], ""
    for location in locations:
        airports = location.get("airports") if isinstance(location, dict) else None
        matches: list[AirportMatch] = []
        seen: set[str] = set()
        for airport in airports if isinstance(airports, list) else []:
            code = airport.get("airport_code") if isinstance(airport, dict) else None
            if not isinstance(code, str) or not _IATA_CODE.match(code) or code in seen:
                continue
            seen.add(code)
            matches.append(AirportMatch(
                iata=code,
                name=str(airport.get("title") or ""),
                city=str(airport.get("city") or ""),
                country=str(_airport_table().get(code, {}).get("country") or ""),
            ))
        if matches:
            return matches[:_MAX_MATCHES], str(location.get("full_name") or "")
    raise ToolError("UPSTREAM_DATA_ERROR: The provider's airport results could not be "
                    "interpreted.")


def _location_search(query: str) -> list[dict]:
    """Call SearchApi's google_flights_location_search engine.

    Returns SearchApi's raw `locations` list: places, each with a nested `airports`
    list (recorded in tests/fixtures/searchapi/location_search_new_york.json).
    `_to_matches` turns it into the tool's result.
    """
    api_key = os.environ.get("SEARCHAPI_API_KEY")
    if not api_key:
        # No upstream call without a credential; raise so the caller never sees
        # an empty result that pretends the upstream was queried.
        raise RuntimeError("SEARCHAPI_API_KEY is not set in the server environment")
    response = httpx.get(
        "https://www.searchapi.io/api/v1/search",
        params={
            "engine": "google_flights_location_search",
            "q": query,
        },
        # The key goes in a header, never the URL: httpx logs URLs and puts them in
        # exception text.
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    locations = payload.get("locations") if isinstance(payload, dict) else None
    if not isinstance(locations, list):
        raise ValueError("unexpected Location Search response shape")
    return locations


@mcp.tool(
    name="search_airports",
    description=(
        "Call this before any flight search whenever the user names a city "
        "or airport instead of giving a three-letter IATA code. Pass every "
        "returned code for that city as the origin or destination list, "
        "unless the user named one specific airport. Do not call it when "
        "IATA codes are already given."
    ),
    structured_output=True,
)
async def search_airports(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="City or airport name to resolve to IATA codes (1-100 chars, trimmed).",
        ),
    ],
) -> AirportSearchResult:
    """Resolve a free-form city or airport name to IATA codes."""
    # Reject whitespace-only inputs after the schema check; the schema allows
    # any non-empty string up to 100 characters, so this catches the "   "
    # case before any upstream request.
    if not query.strip():
        raise ToolError("query must contain at least one non-whitespace character.")

    cache_key = _normalize_key(query)
    cached = CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        locations = _location_search(query)
    except httpx.HTTPStatusError as error:
        # Log the category only; never echo the response body or URL, both
        # of which can carry provider text or request details.
        logger.warning(
            "location_search upstream HTTP %s", error.response.status_code
        )
        raise ToolError(
            "Upstream airport lookup failed (provider returned an error). "
            "Retry shortly, or check the server's SearchApi configuration."
        ) from error
    except httpx.HTTPError as error:
        logger.warning("location_search transport error: %s", error.__class__.__name__)
        raise ToolError(
            "Upstream airport lookup failed (network error). Retry shortly."
        ) from error
    except Exception:
        # Catch-all so an unverified provider payload never reaches the model
        # as raw text. Log the class only; no exception args, which may carry
        # the key or upstream error bodies.
        logger.exception("location_search unexpected failure")
        raise ToolError(
            "Upstream airport lookup failed. Retry shortly, or check the "
            "server's SearchApi configuration."
        )

    # Raises ToolError(UPSTREAM_DATA_ERROR) for a shape it cannot read; nothing is cached.
    matches, place_name = _to_matches(locations)
    if matches:
        note = (f"Showing airports for {place_name!r}, the best of {len(locations)} "
                "matching places." if len(locations) > 1 else None)
        result = AirportSearchResult(matches=matches, message=note)
    else:
        result = AirportSearchResult(
            matches=[],
            message=f"No airport matches for {query!r}; try a different city or airport name.",
        )
    CACHE.set(cache_key, result)
    return result
