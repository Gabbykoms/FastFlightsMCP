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

---

# Amendment 1 (2026-11-24): refill dead branches and settle the timeouts

Status: proposed, for Gabriel's approval. Builds on the implementation in #26.

## Why
Two live runs of the implemented tool (details in `CHECKS.md`) showed problems the offline tests could not:

1. **A search can come back empty although flights exist.** For Chicago → Madrid → Rome → Chicago all three final-leg requests returned the recorded "no results" error, so the beam's three kept branches were all dead and the search ended after 7 requests, with 3 of the 10 allowed unused. In the first live run one of the two leg-2 branches was also dead, as in the recording. Both dead branches so far involved a low-cost carrier (easyJet, Wizz Air), and the beam favors the cheapest projected price. That link is a guess from two cases, not a finding.
2. **The 20 s request timeout is too tight.** Across 15 timed chained requests the times were 1.9, 2.1, 2.2, 2.3, 2.6, 4.1, 5.2, 5.4, 6.9, 9.3, 9.4, 10.0, 19.4 and **28.7** seconds, plus one timeout above 20 s. About one search in eight would hit it. Time does not track response size (28.7 s for 44 KB, 2 s for 1 KB). One timeout currently fails the whole search, and a typical 3-leg search takes about 60 s in a row.

## Decisions to approve
1. **Refill:** a branch that yields nothing usable is replaced by the next-ranked partial trip while the request budget allows, so a dead branch no longer ends the search.
2. **Timeouts for this tool only:** 45 s per request (the slowest success was 28.7 s) and a 2-minute deadline per call. One-way and round-trip searches keep 20 s.
3. **A branch that times out is skipped**, like a dead branch. The search fails with `UPSTREAM_TIMEOUT` only when it ends with no trip and at least one branch timed out.
4. **Out of scope:** running a step's requests in parallel (it would cut a 3-leg search from about 60 s to about 45 s, but SearchApi's rate limits are unchecked), customization, children and infants.

## Behavior
**Refill.** At each step the partial trips are ranked as now (price, travel minutes, id). Branches are expanded in rank order. A branch is *live* when its response gives at least one usable continuation (or, at the last step, at least one candidate); otherwise it is *dead* (the recorded error, no options, or every option filtered). Expansion continues until `width` live branches exist, the ranked partials run out, or the spare budget is gone. Spare budget at a step is `MAX_REQUESTS_PER_CALL − used − width × (steps still to come after this one)`: the refill may never take requests that later steps need for their own `width` branches. At the first expansion stage the spare is 6 for 2 legs, 3 for 3 legs, 0 for 4 legs, 1 for 5 legs (width 2) and 4 for 6 legs (width 1). So refill helps most for 2, 3 and 6 legs, does nothing for 4 and little for 5; that is accepted and stated in `spec.md`.

`exhaustive` stays false whenever any partial trip went unexpanded, the cap was reached or the deadline stopped the search. A search that ends empty after dead branches says "not exhaustive" through the existing message.

**Timeouts.**
- `MULTI_CITY_REQUEST_TIMEOUT_SECONDS = 45` is the per-request timeout of the multi-city client; `REQUEST_TIMEOUT_SECONDS = 20` is untouched.
- `MULTI_CITY_DEADLINE_SECONDS = 120`: checked before each request on a monotonic clock (test hook `_clock`). When it has passed, no new request starts, `exhaustive` is false, and whatever complete trips exist are returned.
- An `httpx.TimeoutException` on a branch counts as a request, is logged by class only, and makes the branch dead. If the search ends with no candidates and at least one such timeout, it raises the timeout (which the tool maps to `UPSTREAM_TIMEOUT`); with candidates it returns them as not exhaustive. Other HTTP and transport errors (401, 429, exhausted 5xx retries, connection errors) still propagate unchanged.

## Step 1: tests first (separate PR, failing by design)
Added to `tests/test_provider.py` (synthetic chains and the recorded dead branch; no new recording, no spend):
- A dead branch among the best three is replaced by the next-ranked partial; the number of live branches reaches `width` when the budget allows, and the request count never exceeds 10.
- Refill never uses requests reserved for later steps (a later step still gets `width` requests); the spare is 6, 3, 0, 1 and 4 requests at the first expansion stage for 2, 3, 4, 5 and 6 legs.
- All branches dead with budget left: every ranked partial is tried, the result is empty and not exhaustive.
- A branch that times out is skipped; trips from the other branches are returned and marked not exhaustive; if every branch times out the call raises the timeout; a non-timeout error (401, 429) still propagates.
- The multi-city client uses a 45 s timeout and one-way and round-trip searches still use 20 s (read from the request's `timeout` extension).
- Deadline: with a fake clock, no request starts after 120 s, complete trips found so far are returned as not exhaustive, and no trips plus a timeout raises the timeout.
- Existing multi-city tests keep passing, including the beam-width and request-count tests.
Added to `tests/test_search_multi_city.py`: a result from a deadline-stopped search carries the existing "not exhaustive" message (the fake adapter returns a `Candidates` with `exhaustive=False`, already covered; add only if a gap appears).

## Step 2: implementation (separate PR)
- `src/fastflights_mcp/provider.py`: the two constants and the `_clock` hook; the step loop in `fetch_multi_city_candidates` expands ranked partials with the refill rule and the deadline check; timeouts on a branch are caught per branch. `_request` and the one-way and round-trip code are unchanged.
- `spec.md` §3.4 and §6: the timeout, deadline and refill rules. `CHECKS.md`: entry with results.

## Verification
- `python3 -m pytest tests -q`; the new tests fail first and pass after. Mutation checks: refill off, spare budget ignored (steals later steps' requests), deadline off, timeout treated as fatal, timeout treated as dead without raising when everything timed out, per-request timeout reverted to 20 s.
- Live (billed, ask first): re-run the Chicago → Madrid → Rome → Chicago search (7 to 10 requests, at most about $0.04) and expect trips, or an honest empty result after trying more branches. Record each request's time.

## Open risks
- Refill spends more requests per call (up to the cap of 10), so a search can cost up to about $0.04.
- The per-request timeout and deadline come from 15 samples taken on one evening.
- If most branches are dead for a route, refill will not fix that; the branch ranking itself may need diversity (for example a penalty or a cap per carrier). Not planned until there is evidence.
