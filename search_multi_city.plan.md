# Plan: `search_multi_city` (complete-itinerary search, 2–6 legs)

Status: approved by Gabriel, 2026-11-24. Owner: Gabriel.

## Context
Week 9 is done (`search_airports`, `search_flights`). `search_multi_city` is Gabriel's next tool (`plan.md`: Gabriel owns it; intent `intent/search-multi-city.md` approved by Nadia and Patrick 2026-11-22; contract in `spec.md` §3.4). The user wants a plan committed to the repo as `search_multi_city.plan` first, then tests-first work.

Decisions made in this session (2026-11-24):
1. **Scope:** complete-itinerary search only. Per-leg customization (`customization`, server-held snapshots, `STALE_SELECTION`) is a later tests-first PR once the snapshot TTL and session scope are decided.
2. **Search breadth:** beam of 3 inside the existing 10-request cap.
3. **Same city:** same `airportsdata` city name, or same country and ≤100 km apart (lat/lon from `airportsdata`).

How SearchApi does multi-city (docs, unverified by recording): `flight_type=multi_city` with `multi_city_json` = `[{"departure_id","arrival_id","outbound_date"}, ...]`. One request returns leg-1 options. Each option's `departure_token` fetches the next leg's options given that choice. `price` is the whole-trip total. `booking_token` on the last leg only adds booking details, which we don't need.

## Step 0: commit the plan (first action after approval)
- This file, committed on its own branch first.
- Commit it on a new branch `plan/search-multi-city`.
- Show the PR text, then open the PR after approval.

## Step 1: record fixtures (billed; ask before each live request)
- One 3-leg chain JFK,EWR,LGA → LHR,LGW → CDG,ORY → JFK,EWR,LGA, about 5 weeks out, economy, 1 adult.
- 3 requests (one per leg, following the first usable option's token), at most about $0.012.
- Save sanitized `tests/fixtures/searchapi/multi_city_leg1.json`, `_leg2.json` and `_leg3.json`, plus a README entry. Strip the key and tokens with the same method as #14.
- Verify from the recording:
  - multiple airports per `departure_id`
  - `price` on the last leg is the trip total
  - the field names (`best_flights` / `other_flights`, `departure_token`)
  - local times without offsets (handled by the existing `_timestamp` plus `airportsdata` time zones)

## Step 2: failing tests PR (tests only, committed separately)
**`tests/test_search_multi_city.py`**
- Runs through in-memory `Client(mcp)` with a fake adapter on `server.fetch_multi_city_candidates`, the same pattern as `tests/test_search_flights.py` (its `no_network`, `server`, `backend` and `candidate` helpers).
- Schema/discovery: `legs` 2–6, per-leg `max_stops`, unknown arguments rejected, wrong types never run the tool body.
- Validation before upstream:
  - leg count
  - dates decreasing
  - first date in the past
  - a city gap (NYC→LON then PAR→ROM)
  - a leg whose origin and destination are the same city
  - children and infants rejected (`UNSUPPORTED_FILTER`)
  - `strict_budget` without a budget
- Feasibility:
  - the next leg departs before the previous arrival → dropped
  - an airport change in the same city at 23h59m is dropped, and at exactly 24h is allowed with a transfer note
  - elapsed time uses UTC offsets
- Completeness: every result has all legs; no partial trips; a missing leg gives an empty result with the "no complete itinerary" message.
- Filters: top-level `max_stops` applies to every leg, and a per-leg `max_stops` overrides it for that leg only.
- Ranking and budget, reused rules:
  - total duration sums leg durations including connections and excludes city visits
  - city visits are not stops
  - the 60-minute tie goes to price
  - 3+2 allocation, inclusive 20%, strict ceiling, unused slots
- Not exhaustive: the message says so when the beam or cap pruned anything.
- Upstream failures map to the shared categories (reusing `_upstream_error`).

**`tests/test_provider.py`** (add a multi-city section), using `httpx.MockTransport` and the Step 1 fixtures:
- Request params: `flight_type=multi_city`, a valid `multi_city_json`, the key in the header only.
- Token chaining: leg 2 is requested with leg 1's token.
- Beam width 3. Request counts: 2 legs = 4, 3 legs = 7, 4 legs = 10. 5 and 6 legs shrink the width to fit the cap.
- The cap is never exceeded, and retries count.
- Final price taken from the last leg, divided by adults.
- Leg airports outside the requested sets are dropped.
- Unusable options are dropped, and all-unusable gives `UPSTREAM_DATA_ERROR`.
- `CONFIGURATION_ERROR` without the key, with no request made.

PR title example: "Tests only: search_multi_city (2–6 legs, city continuity, 24-hour airport change, beam-limited SearchApi chain, budget rules); N failing by design".

## Step 3: implementation PR
**`src/fastflights_mcp/provider.py`**: add `fetch_multi_city_candidates(**query) -> Candidates`.
- Reuse `_request` (cap and retry), `_options`, `_link`, `map_leg`, `_price`, `_per_person`, `_within`, `_identify`, `_Usage`, `Candidates` and `UnusableOption`.
- New `_multi_city_params(query, departure_token=None)` builds `multi_city_json`.
- Beam search:
  - Step 1 gives partial trips.
  - Keep only feasible partials: per-leg `max_stops`, chronology, and the airport-change rule against the previous leg.
  - Rank partials by (price, duration).
  - Expand the best `w` with their `departure_token`, where `w = min(3, (MAX_REQUESTS_PER_CALL - 1) // (legs - 1))`.
  - Partials from the last leg become candidates priced by the last leg's whole-trip `price`.
- `exhaustive=False` whenever partials were pruned or `_CapReached` was hit.

**`src/fastflights_mcp/tools/search_multi_city.py`** (new), mirroring `search_flights.py`:
- `LegRequest` (pydantic, `extra="forbid"`): `origin: AirportSet`, `destination: AirportSet`, `depart_date: DateString`, `max_stops` 0–2 or null.
- Tool args: `legs` (2–6), `cabin`, passengers, `currency`, `budget`, `strict_budget`, `max_stops`. No `customization` yet.
- `_same_city(a, b)`: `airportsdata` city match, or same country and haversine ≤100 km.
- `_validate`:
  - date order
  - past date
  - city continuity: every airport pair in `legs[i].destination` × `legs[i+1].origin` is the same city
  - a leg must not stay in one city
  - children and infants rejected
- `_normalize`: all legs present, each leg inside its sets, chronology, airport change ≥24h, per-leg and top-level stops.
- Then reuse `_rank`, `_select`, `_reasons`, `Itinerary`, `FlightSearchResult` and `_upstream_error`, imported from `tools/search_flights.py` (no copy).
- The message discloses airport transfers and non-exhaustive search.
- `register(app)` with the same `extra="forbid"` closing as `search_flights.register`.

**`src/fastflights_mcp/server.py`**: add the `fetch_multi_city_candidates` hook that delegates to `provider`, and register the tool.

**Docs:** `spec.md` §3.4/§6 record the three decisions and that customization is deferred; `CHECKS.md` gets an entry; README status line and `search_multi_city` section.

## Verification
- `python3 -m pytest tests -q`: the new tests fail on the tests PR and pass on the implementation PR. Existing 163 stay green, and Patrick's 84 baseline errors are unchanged.
- Mutation checks: break same-city, the 24h boundary, the beam width, per-leg `max_stops` and chronology; each must make a test fail.
- Live (billed, ask first): one `client.py`-style call and one fresh Claude Code session asking for "New York → London Mar 10 2027, London → Paris Mar 14, Paris → New York Mar 20, economy, 1 adult". Restart Claude Code after the code changes. At most about 10 requests ($0.04).
- Scan every fixture and transcript for the key and tokens before committing.

## Not in this work
Customization and `STALE_SELECTION`, children and infant pricing, caching, separate-ticket detection beyond `None` (unknown).
