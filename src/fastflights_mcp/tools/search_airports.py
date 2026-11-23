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
import logging
import os
import re
from typing import Annotated, Optional

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


def _location_search(query: str) -> list[dict]:
    """Call SearchApi's google_flights_location_search engine.

    NOTE: the field mapping below (iata/name/city/country) is unverified
    until a real fixture is recorded. Sanitize and verify before promising
    these field names to callers.
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
            "query": query,
            "api_key": api_key,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    locations = payload.get("locations") or payload.get("results") or []
    return list(locations)


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
        # of which can carry the api_key parameter.
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

    matches = [
        AirportMatch(
            iata=str(item.get("iata", "")),
            name=str(item.get("name", "")),
            city=str(item.get("city", "")),
            country=str(item.get("country", "")),
        )
        for item in locations[:_MAX_MATCHES]
    ]
    if matches:
        result = AirportSearchResult(matches=matches, message=None)
    else:
        result = AirportSearchResult(
            matches=[],
            message=f"No airport matches for {query!r}; try a different city or airport name.",
        )
    CACHE.set(cache_key, result)
    return result
