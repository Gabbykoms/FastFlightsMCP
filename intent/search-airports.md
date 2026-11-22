# FastFlightsMCP: Airport lookup

Status: Draft, agreed by team 2026-11-22.
Named team approver: Nadia Boudhaim Maguire

## Purpose

`search_airports` resolves a city or airport name to IATA codes so the model doesn't have to guess them before calling `search_flights`. The data source is the SearchApi Google Flights Location Search endpoint, using the same API key and HTTP client as the other tools.

## User experience

The model calls `search_airports("new york")` when it has a city and needs the IATA. The tool returns up to 10 airports in the order the API returned them, each with `iata`, `name`, `city`, `country`. The model passes all relevant matches as the origin or destination list for `search_flights`, or only the airport the user named, if they named one. When the list is empty the model rephrases and tries again — there is no fuzzy fallback of our own.

A normal result with an empty `matches` list and a message means "nothing matched"; the model should recover. A `ToolError` only happens when the upstream itself fails (missing key, HTTP error, quota).

## Success criteria

- The Location Search response includes IATA codes; this is verified against a recorded fixture before shipping.
- Results are capped at 10, in API order, with no `type` field and no ranking of our own.
- Each hit has exactly: `iata` (3-letter airport code), `name`, `city`, `country`.
- A query that matches nothing returns a normal result with `matches: []` and a message; not a `ToolError`.
- A query the upstream cannot answer (HTTP error, missing key, quota) raises `ToolError` with a message the model can act on. The key itself is never in the message.
- Out of scope: reverse lookup (IATA → city), airlines, train stations, ICAO/FAA codes.
- Tests cover, with a fake upstream: schema listing, wrong-type rejection without running the function, out-of-range input, empty result, upstream failure.
- Location Search responses are cached, keyed by the normalized query, so repeating a lookup does not call SearchApi again or spend another search. The cache is Redis, which gives fast, low-cost lookups that are safe under concurrent access and shared by both hosts (Claude Code and our loop).
- Test fixtures come from a real Location Search response, recorded once with the API key removed.
- If Redis is not running, the server logs a warning and works without the cache; lookups still succeed.
- Tests use a fake cache, so they need neither Redis nor an API key.
- Cached lookups stay valid for 30 days, since airport lists rarely change.

## Open decisions

- Which Redis client package and version to use, recorded in spec.md under Dependencies.
