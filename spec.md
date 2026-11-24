# FastFlightsMCP: Specification

**Status:** Draft — specification decisions in progress; not approved for implementation
**Date:** 2026-11-22
**Inputs:** `intent/*.md`, `README.md`, [Team Project 2 brief](https://github.com/kousen/ai-integration-course/blob/main/assignments/team-project-2.md), [worked MCP example](https://github.com/kousen/ai-integration-course/tree/main/examples/mcp-server)
**No code is written in this document.** Interfaces are signatures and payload shapes only.

## 1. The task

Help a traveler turn a plain-language flight request into real, comparable
flight options: resolve cities to airports, find dates when the traveler is
flexible, search the requested itinerary, and present up to five eligible
options with one explained recommendation. Support complete multi-city trips
and per-leg customization as defined in the intents. Report available price
insights without inventing prices or predicting future fares. The server
provides structured data; the host handles clarification and presentation.
The project does not book flights or process payments.

This implementation covers the complete product and its public release, not
only the initial Week 9 tools. The lab's milestones guide the work order rather
than limit the delivered scope.

Gabriel owns hosted deployment and package distribution, including publishing
to PyPI for use through `uvx`. Both remain required final-release deliverables.
The purpose is to let people outside the team either connect to the hosted
server or install and run it locally. This is an explicit project scope decision,
independent of the minimum course requirements. The linked written brief permits
local stdio without remote hosting. Nadia reported a separate verbal grading
expectation about deployment; that clarification is not treated as verified
written policy here and does not change our chosen release scope.

The current implementation milestone covers the entire product described below:
all five tools, both hosts, caching, evaluation, hosted deployment, and package
publishing. The two-tool Week 9 lab is an intermediate verification checkpoint,
not the completion boundary. Internal sequencing does not defer any of these
deliverables to a separate implementation milestone.

The implementation includes:

- All five planned tools: `search_airports`, `search_flights` (one-way and
  round-trip), `search_multi_city`, `find_cheapest_dates`, and
  `get_price_insight`, with behavior and acceptance criteria from their intents.
- Complete-itinerary comparisons, per-leg customization, airport resolution,
  budget-aware recommendations, and the agreed passenger, cabin, filtering,
  ranking, and clarification rules. The host presents only complete eligible
  trips and never invents unavailable fares or flight details.
- An MCP server usable by compatible external hosts, plus the project's own
  agent loop and a no-model client for protocol verification.
- Caching, rate limiting, input validation, actionable upstream failures,
  credential handling, and automated offline tests for all tools.
- A published Python package with the `fastflights-mcp` entry point, runnable
  through `uvx fastflights-mcp`, with installation, configuration, and host
  connection instructions for people outside the team.
- Local stdio operation and remote hosting through streamable HTTP. The
  deployment, access controls, credential ownership, and operating-cost model
  must be specified before the hosted service is released.

Completion means all five tools and the agent loop are implemented and verified,
the package is published and tested from a clean external installation, and
the hosted server is deployed and verified from an external compatible client.
Release documentation must explain required credentials, supported workflows,
limits, and how users confirm fares through the returned booking-site links.

Price-watch alerts are deferred and are not required for this release.
Whether baggage/fare-class details, listed in the README as a future idea,
belong in this release remains to be confirmed; their behavior is not yet
specified. City-to-airport expansion is already included through the existing
search intents.

## 2. Global decisions

### Language
Python 3.11+, as established in the README and required by the lab. JavaScript/
TypeScript is an alternative, but would not meet the lab's Python requirement.
The lab requires the official `mcp` SDK v2 and `MCPServer`; the linked course example reports testing version 2.3.0. Verify the installed
version and interfaces when implementing rather than assuming compatibility.

### Model
Use the OpenRouter SDK with one tool-capable model per run and no streaming.
The provisional low-cost baseline is `minimax/minimax-m3`; the comparison
candidate is `anthropic/claude-haiku-4.5`, chosen to test whether higher cost
improves task completion and tool-use reliability. "Stronger" is the evaluation
hypothesis, not a measured project result. Compare both on the same prompts,
tool schemas, and caps; record task correctness, invalid calls, cap hits,
latency, and actual cost before choosing the final model. Do not switch models
mid-run to hide a failed evaluation.

The model IDs and tool-use offerings are documented by OpenRouter:
[MiniMax M3](https://openrouter.ai/minimax/minimax-m3) and
[Claude Haiku 4.5](https://openrouter.ai/anthropic/claude-haiku-4.5/api).
Check endpoint tool support and pricing at evaluation time. No model is embedded
in the server or the no-model verification client.

### Data provider and airport selection

**Decided:** use SearchApi for all five tools. The server calls its flight,
calendar, and location-search engines through `httpx`; it does not scrape
Google Flights HTML itself. Keep `SEARCHAPI_API_KEY` in the server environment.
Provider response mappings still require sanitized fixture verification.

**Decided:** use AirportSet arrays for all relevant airports returned for a city;
preserve a specific user-requested airport as a singleton. Do not arbitrarily
pick one city airport. Nadia owns aligning the airport intent with this decision.
Use the provider's multi-airport query support where applicable instead of
unnecessarily issuing one request per airport combination.

## 3. The server's tools

These are draft public contracts, not provider payloads. Rules already agreed
in `intent/*.md` remain requirements. New schema choices, numeric bounds, and
responsibility assignments below are **proposals for team review**, not approvals.
SearchApi is the chosen provider; individual response mappings remain provisional
until fixture validation. No live paid calls have been made to validate these contracts.

### 3.1 Shared contract and responsibilities

**Proposed ownership:** the server validates inputs, resolves provider responses,
enforces hard filters and budget eligibility, ranks candidates, and selects the
returned options. The host collects missing information, resolves city names
through `search_airports`, and explains the structured results. A server never
asks the traveler a question directly. Hosts must not relax constraints silently.

**Shared input types:**

| Type / argument | Contract | Limits / defaults |
|---|---|---|
| AirportSet | Array of distinct uppercase three-letter IATA codes | Proposed 1–10; a specific requested airport becomes a singleton. Raw city names are resolved first. |
| Date | String, `YYYY-MM-DD`, valid calendar date | Proposed: today or later, using UTC for the server's input-date boundary; provider booking horizon must be verified. |
| `cabin` | `economy`, `premium_economy`, `business`, `first` | Required for travel tools; host asks when missing. |
| `adults` | Strict integer | Proposed 1–9; default 1. |
| `children` | Strict integer | Proposed 0–8; default 0. |
| `infants_in_seat`, `infants_on_lap` | Strict integers | Proposed 0–8 each; default 0; lap infants cannot exceed adults. Split replaces README's ambiguous `infants` argument, subject to review. |
| Passenger party | Sum of all passenger counts | Proposed maximum 9, same party across legs. |
| `currency` | Uppercase three-letter currency code | Default USD; must also be supported by the configured provider. |
| `budget` | Positive finite decimal amount or null | Default null; per person for the entire itinerary, in `currency`. |
| `strict_budget` | Boolean | Default false; true requires `budget`. |
| `max_stops` | Strict integer or null | Proposed 0–2 or null for unrestricted; applies per requested leg. |

Reject unknown arguments, booleans supplied as integers, numeric strings supplied
as counts, invalid enums, and out-of-range values before executing the tool body.
Cross-field validation must finish before any upstream request. Use strict
schema validation rather than silently coercing malformed calls.

**Common success envelope:** `query` (normalized public inputs), `fetched_at`
(UTC timestamp of the underlying data), `cache_hit` (boolean), `message`
(string or null), and the tool-specific fields below. Travel tools echo cabin,
passengers, and currency. Cached responses preserve the original fetch time.
Do not expose credentials, authenticated request URLs, or raw provider payloads.

**Shared flight result types:**

| Type | Fields |
|---|---|
| Segment | `airline`, `flight_number` (nullable), `origin`, `destination`, `departure`, `arrival`, `duration_minutes` |
| FlightLeg | `origin`, `destination`, `departure`, `arrival`, `duration_minutes`, `stops`, `segments: Segment[]`, `google_flights_url` |
| Itinerary | `id`, `legs: FlightLeg[]`, `price_per_person`, `currency`, `total_travel_minutes`, `total_stops`, `google_flights_url`, `separate_tickets: boolean or null`, `budget_status`, `budget_overage`, `recommendation_reasons: string[]` |
| FlightSearchResult | Common envelope plus `results: Itinerary[]`, `recommended_id: string or null`, `within_budget_found: boolean or null` |

Times include the local date and UTC offset; elapsed durations use actual
instants, not subtraction of local clock readings. IDs identify immutable
selected flight combinations, not provider ranking positions. The source of
per-person pricing for mixed passenger parties must be verified: never relabel
a party total as an individual fare or silently divide it into an asserted fare.
Required price, time, airport, or completeness information that cannot be
validated makes a candidate unusable. If the response cannot be interpreted
reliably, return an upstream-data error rather than claiming no flights exist.
Optional missing values are null; unknown ticket structure is not false.

`budget_status` is `not_specified`, `within_budget`, or `over_budget`;
`budget_overage` is zero when within budget and null when no budget is supplied.
`within_budget_found` is null without a budget. Currency conversion is not part
of these tools. Links open the corresponding Google Flights search/selection;
a link is not a reservation or guaranteed fare.

**Selection policy:** `search_flights` and `search_multi_city` return at most
five complete itineraries across all airport combinations. Follow the intents'
three-within/two-alternative allocation and unused-slot rules. Alternatives
must fit the inclusive 20% allowance and, when within-budget results exist,
rank better than the best within-budget result. Strict budgets prohibit overages.
Recommend within budget whenever possible; otherwise explicitly mark the best
eligible alternative. With no budget, return the five best eligible options.
Use the intents' duration, connecting-stop, and one-hour price-tiebreak rules;
requested city visits are not connecting stops. Do not claim exhaustive coverage
if an upstream request limit prevented finishing the search.

**Shared errors:** schema rejection is an MCP error result with a field-specific
correction. Operational failures raise `ToolError` with a stable category and
sanitized actionable message: `CONFIGURATION_ERROR` (operator must configure
credentials), `UPSTREAM_AUTH_ERROR` (operator must check credentials),
`RATE_LIMITED` (retry later, with retry time only if known), `UPSTREAM_TIMEOUT`,
`UPSTREAM_UNAVAILABLE`, `UPSTREAM_DATA_ERROR`, `UNSUPPORTED_FILTER`, or
`SEARCH_LIMIT_EXCEEDED` (narrow the request). Do not convert failures into empty
successes. Do not echo provider error text without sanitizing it. An unsupported
hard filter is rejected rather than ignored. Per-call upstream request caps,
timeouts, and retry counts must be settled in section 6 before implementation.

### 3.2 `search_airports`

**When to call:** resolve a city or airport name before a travel search when
IATA codes are not already supplied. Not a reverse-lookup tool.

| Argument | Type | Required | Limits / notes |
|---|---|---|---|
| `query` | String | Yes | Proposed 1–100 characters after trimming; reject whitespace-only input. |

**Result:** common envelope plus `matches`, an array of up to 10 objects with
exactly `iata`, `name`, `city`, and `country`, all strings. Preserve upstream
order among valid airport matches; do not return stations or invented IATA codes.
The initial server checkpoint may run without a cache, but milestone completion
requires caching. For the complete implementation, cache
by normalized query; proposed normalization is trim, collapse whitespace, and
case-fold. Backend, TTL, and unavailable-cache behavior remain open.

**Not found:** `matches: []` with an explanatory message, normal success. The
host may rephrase the query within its step/cost caps.

**Errors:** shared errors, including missing key, quota, and malformed upstream
results. Verify a recorded fixture contains every required airport field before
promising this mapping. City expansion follows the decided AirportSet policy
in section 2; the older pick-one wording in the airport intent is superseded.

**Recorded provider behavior (2026-11-23):** Location Search takes the text as `q`, not
`query`. It returns places (mostly cities, best match first), each with a nested
`airports` list of `airport_code`, `title`, `city` and `distance`, and **no country**.
The tool returns the airports of the best matching place only, takes `country` from
`airportsdata`, skips duplicate and malformed codes, and reports a non-empty response
with no readable airports as `UPSTREAM_DATA_ERROR`. Only a city-name query is recorded;
airport-name queries are not.

### 3.3 `search_flights`

**When to call:** find complete one-way or round-trip options after locations,
required dates, and cabin are known. Resolve city names first.

| Argument | Type | Required | Limits / notes |
|---|---|---|---|
| `origin`, `destination` | AirportSet | Yes | Proposed arrays support all resolved airports in one combined search. |
| `depart_date` | Date | Yes | Local departure date. |
| `return_date` | Date or null | No | Null means one-way; otherwise round-trip, on or after departure date. |
| `cabin` | Shared cabin enum | Yes | No silent default. |
| Passenger arguments, `currency` | Shared types | No | Shared defaults. |
| `budget`, `strict_budget`, `max_stops` | Shared types | No | Shared defaults and whole-trip budget rules. |

**Result:** `FlightSearchResult`; exactly one requested leg per one-way result
and two per round-trip result. The return leg travels back between the requested
location sets. Validate actual chronological feasibility, including overnight
arrivals. Intermediate outbound selections without a confirmed return and total
fare are not complete round trips. Follow provider selection steps internally.
Proposed omission of public `sort_by` and `limit` keeps the agreed ranking and
five-result behavior consistent; this supersedes the README sketch if approved.

**Not found:** `results: []`, `recommended_id: null`, and a message identifying
that no eligible complete itinerary was found. No budget relaxation beyond the
agreed allowance. The host offers to adjust preferences.

**Errors:** shared errors; invalid date order or unusable airport selections
must not become successful empty responses. Provider failure during completion
of round trips is reported as a failure, not as evidence of no availability.

**Interim limitation (decided by Gabriel):** until the mixed-party price basis is
settled, a search with `children`, `infants_in_seat` or `infants_on_lap` above zero
is rejected with `UNSUPPORTED_FILTER` before any upstream request. SearchApi returns
one party total, and recordings show a child's fare can differ from an adult's (for
example 993 instead of 1042 on one option), so a per-person price cannot be derived
without asserting a fare. The arguments stay in the schema. Adults-only parties are
priced as the provider's total divided by the number of adults.

### 3.4 `search_multi_city`

**When to call:** search a specified sequence of 2–6 legs, or replace one leg
of a previously returned itinerary while holding the other flights fixed.

| Argument | Type | Required | Limits / notes |
|---|---|---|---|
| `legs` | Array of LegRequest | Yes | 2–6 entries in travel order. |
| LegRequest | Object | Per leg | `origin: AirportSet`, `destination: AirportSet`, `depart_date: Date`, optional `max_stops`. |
| `cabin` | Shared cabin enum | Yes | Same cabin on all legs. |
| Passenger arguments, `currency` | Shared types | No | Same party and currency throughout. |
| `budget`, `strict_budget`, `max_stops` | Shared types | No | Whole-trip budget; top-level stop limit applies to every leg. |
| `customization` | Object or null | No | Proposed `{itinerary_id: string, leg_index: integer}`; index is zero-based and in range. |

Dates must be nondecreasing; actual flights must also arrive before the next
leg departs. Validate city continuity using trusted location metadata, not IATA
string similarity. Airport changes within the same city require at least 24
elapsed hours between arrival and onward departure. Never insert a missing leg.
A supplied per-leg stop limit overrides the top-level limit for that leg only;
the host may supply an exception only when the user explicitly requests it.

For customization, resolve the itinerary ID to a server-held snapshot scoped to
the caller/session; require all unchanged inputs and flights to match it. Recheck
availability and the complete fare. Proposed ID expiry follows the flight-cache
TTL, which remains open. A missing, expired, or mismatched ID returns
`STALE_SELECTION` with instructions to search again, not a silent new selection.
Do not accept arbitrary provider tokens or authenticated URLs as IDs.

**Result:** `FlightSearchResult`, with all requested legs in every itinerary.
During customization, each result is a complete revised trip with the other
selected flights fixed; the host shows the changed leg and updated whole-trip
price. Return at most five options and disclose airport transfers and known
separate-ticket requirements. Apply the same eligibility rules after changes.

**Not found:** no partial trips or standalone available legs. Return an empty
result and explain that no complete eligible combination could be formed.

**Errors:** shared errors plus `STALE_SELECTION`; reject invalid leg count,
route gaps, or conflicting dates before upstream search where detectable.
Failure to finish provider selection must not produce a made-up total fare.
Fixture-based proof of complete multi-city pricing and customization is required
before release; an upstream single-leg response is not sufficient evidence.

**Implemented so far (2026-11-24): complete-itinerary search only.** `customization` and
`STALE_SELECTION` are not implemented, so the tool has no `customization` argument yet; they
wait for the snapshot expiry and session scope decisions above. Decisions and recorded
provider behavior (`tests/fixtures/searchapi/README.md`, "Multi-city"):

- SearchApi answers one leg per request. A trip of N legs is a chain of N requests, each
  following an option's `departure_token`; the last leg's `price` is the whole-trip total
  (not verified to equal the sum of three separate fares; 2+ adults not recorded).
- Trying every combination would exceed the 10-request cap, so each step continues only the
  best `min(3, (10 - 1) // (legs - 1))` partial trips, ranked by projected price, then travel
  time. That is 4, 7, 10, 9 and 6 requests for 2, 3, 4, 5 and 6 legs. Whenever a partial trip
  was left out, or the cap was reached, the result says it is not exhaustive.
- A token can lead nowhere: HTTP 200 with the `error` "Google Flights didn't return any
  results." That exact text is a dead branch; every other `error` body is
  `UPSTREAM_DATA_ERROR`. A dead branch, and a branch whose request times out, is replaced by the
  next-ranked partial trip while the spare request budget allows (at the first stage: 6, 3, 0, 1
  and 4 spare requests for 2, 3, 4, 5 and 6 legs); the spare never takes requests a later stage
  needs. Measured 2026-11-24: a search with all three kept branches dead returned nothing after
  7 of 10 requests, which is why refill exists.
- Timeouts for this tool: 45 seconds per request (chained requests took 1.9 to 28.7 s in 15
  samples from one evening, and one passed 20 s) and a 2-minute deadline per call; after the
  deadline no request starts and the result is not exhaustive. One-way and round-trip searches keep
  20 seconds. A search that ends with no trip and at least one timed-out branch fails with
  `UPSTREAM_TIMEOUT`; with trips it returns them as not exhaustive.
- Same city (`fastflights_mcp.places.same_city`): same country, and either the same
  `airportsdata` city name and subdivision or at most 100 km apart. Every airport of a leg's
  destination set must be in the same city as every airport of the next leg's origin set, and
  a leg whose every origin and destination pair is in one city is rejected.
- The 24 hours for an airport change are elapsed time from UTC offsets. Children and infants
  are rejected with `UNSUPPORTED_FILTER`, as in `search_flights` (section 3.3).
- No new dependency: `places` reuses the pinned `airportsdata`.

### 3.5 `find_cheapest_dates`

**When to call:** a traveler supplies a route and a flexible date window and
wants inexpensive dates before choosing exact flights.

| Argument | Type | Required | Limits / notes |
|---|---|---|---|
| `origin`, `destination` | AirportSet | Yes | Same location handling as flight search. |
| `trip_type` | `one_way` or `round_trip` | Yes | Explicit; do not infer from absent fields. |
| `depart_start`, `depart_end` | Date | Yes | Inclusive ordered window. |
| `return_start`, `return_end` | Date or null | For round trips | Both required for round trips, forbidden for one-way. |
| `nights` | Strict integer or null | No | Proposed 1–365; round-trip only; filters date pairs to a fixed stay. |
| `cabin` | Shared cabin enum | Yes | Required. |
| Passenger arguments, `currency`, `max_stops` | Shared types | No | Shared defaults. |
| `limit` | Strict integer | No | Proposed 1–10, default 5. |

Proposed initial trip-length interface: explicit return window with optional
fixed nights. The host converts a stated fixed stay into a return window; the
server filters to matching pairs. Variable min/max-night support is deferred
pending review. Return must be after departure for each retained pair.

Reject oversized windows before a paid request: proposed maximum 200 inclusive
outbound days for one-way, or 200 date combinations in the full rectangular
outbound/return window for round-trip, before filtering by nights or valid order.
Do not silently split into additional billed searches. Airport expansion must
also fit the eventual per-call request cap.

**Result:** common envelope plus `options: DateOption[]`,
`no_flights: DatePair[]`, and `lowest_price: decimal or null`.
`DatePair` contains `depart_date` and nullable `return_date`; `DateOption` adds
`price_per_person` and `is_lowest_price`. Sort priced options ascending, then by
departure and return date for deterministic ties; flag all tied cheapest options.
`no_flights` reports explicitly confirmed unavailable dates independently of the
top-option limit. Missing upstream entries are not automatically unavailable;
incomplete coverage must be reported as an upstream-data failure. Echo the exact
searched windows, nights, and requested currency.

**Not found:** `options: []`, `lowest_price: null`, explicit unavailable date
pairs where confirmed, and a normal explanatory message.

**Errors:** shared errors; invalid windows and excessive combinations are
validation errors. A provider empty response to an invalid request must not be
misrepresented as genuine no-flight dates. Validate one-way and fixed-stay
mappings with fixtures before implementation is accepted.

### 3.6 `get_price_insight`

**When to call:** explain the provider's current price signal or historical
prices for a specified one-way or round-trip search. Not a forecast or a claim
about one particular flight number.

| Argument | Type | Required | Limits / notes |
|---|---|---|---|
| `origin`, `destination` | AirportSet | Yes | Insights must match this exact route scope. |
| `depart_date`, `return_date` | Date, Date or null | Yes / No | Same date semantics as `search_flights`. |
| `cabin` | Shared cabin enum | Yes | Required. |
| Passenger arguments, `currency`, `max_stops` | Shared types | No | Shared defaults. |
| `comparison_dates` | Array of Date strings | No | Proposed 0–10 unique historical observation dates, no later than today; these are not travel dates. |

**Result:** common envelope plus `available: boolean`,
`lowest_price: decimal or null`, `price_level: string or null`,
`typical_range: {low: decimal, high: decimal} or null`,
`history: [{observed_date: date, price: decimal}]`,
`comparisons: [{requested_date: date, observed_date: date or null, price: decimal or null, status: matched or unavailable}]`,
and `notice: string`. Prices use the same verified per-person basis as flight
results. Preserve the provider's signal; do not calculate a substitute label.
The supported signal vocabulary must be verified before narrowing the string
to an enum. History is sorted chronologically and retains actual observations.

Proposed comparison policy: exact observation dates only; missing dates return
`unavailable` rather than interpolated prices. Return all available history,
with no promise of a fixed lookback. Do not synthesize an aggregate insight
from separate airport searches. Reuse a cached equivalent flight response when
available; otherwise a fresh lookup may consume another billed search.
The notice states that historical prices do not predict future fares and that
current fares must be confirmed on the booking site. Omit estimated future
savings from the initial contract pending an explicit product decision.

**Not found:** `available: false`, null insight values, empty history, and an
explanation when the provider supplies no insight. Partially populated insights
remain available with missing fields explicitly null and explained.

**Errors:** shared errors. A failed lookup is not the same as absent insight.
Unsupported comparison scope or unverifiable price basis is reported explicitly,
not replaced with invented historical observations.

### 3.7 Verification and provider references

Every tool requires offline MCP tests for discovery/schema, strict wrong-type
rejection without executing the function, bounds/cross-field validation, normal
empty results, and simulated upstream failures. Add meaningful cases for
complete-trip selection, budget boundaries, 24-hour airport changes, stale
customization IDs, calendar caps, and missing history. Fake upstream and cache
interfaces so tests require neither credentials nor a Redis service. Capture
sanitized real fixtures separately before asserting provider mappings work.

Provider documentation reviewed for this draft:

- [Flights API](https://www.searchapi.io/docs/google-flights-api): documents
  passenger categories and selection tokens. Completing a multi-leg search can
  require successive requests; a single initial result is not assumed complete.
- [Calendar API](https://www.searchapi.io/docs/google-flights-calendar-api):
  documents date windows and search-size restrictions. The bounds above are
  deliberately enforced locally rather than trusting an oversized empty response.
- [Location Search API](https://www.searchapi.io/docs/google-flights-location-search-api):
  proposed airport-lookup adapter source, subject to recorded-fixture verification.

## 4. Components

| Component | Responsibility and interfaces | Language and model decisions | Failure handling |
|---|---|---|---|
| MCP server (`server.py`, package entry point) | Exposes section 3 tools over stdio and streamable HTTP; shares the same implementation between both transports. | Python as required by the course; TypeScript considered but incompatible with that requirement. No model: deterministic validation and provider access belong in code rather than an embedded LLM. | Strict validation, sanitized `ToolError`, stderr logging, request limits; no protocol output through `print`. |
| SearchApi adapter | Uses `httpx` to map typed requests to SearchApi and normalize responses; credentials stay server-side. | Python to share server types; a separate Node service would add an unnecessary deployment boundary. No model: explicit mappings are testable and must not invent fares. | Bound request time, count every attempted request, sanitize upstream errors, reject unusable data; fixtures replace HTTP in tests. |
| Own loop (`agent.py`) | Discovers tools through MCP, sends schemas to OpenRouter, dispatches tool calls, and prints recommendations and sanitized step/cost logs. | Python and the OpenRouter SDK per brief; raw HTTP model calls considered but the SDK is required. Model candidates and evaluation decision are in section 2. | Pass sanitized tool errors back unchanged, enforce section 6 caps, stop explicitly on exhaustion. Missing required prompt details produce an actionable incomplete-task result; the noninteractive loop cannot invent them. |
| Verification client (`client.py`) | Lists schemas and exercises tools through MCP without a model; uses the same protocol surface as the hosts. | Python for the official SDK; shell-only checks would not provide the same structured assertions. No model, deliberately, so protocol behavior can be inspected without model variability or token cost. | Display error results and fail clearly on connection/protocol errors. Live SearchApi calls can still incur search charges even without a model; offline fixtures do not. |
| Cache (full implementation) | Reuses normalized queries across repeated requests. Caching is required for milestone completion; backend remains under review. | Python cache interface; Redis offers sharing across server processes, while in-memory storage avoids an extra service but is process-local and lost on restart. No model; cache policy is deterministic. | Offline tests use a fake cache. Set TTL and outage policy before enabling the selected backend. |
| Distribution and hosted service (Gabriel) | Publishes the package and verifies clean `uvx` installation; deploys the HTTP server and verifies external-client access. | Reuse the Python server instead of a second-language implementation. No additional model in deployment; clients supply their own host models. | Release requires credential isolation, access control, spending limits, and installation/connection checks. Hosting platform and access-control implementation remain to be selected. |

Claude Code is the other required host: configure it through `.mcp.json` and
save evidence of the same task being completed through the same tools. Its
model configuration belongs to the host; the MCP server does not choose it.

## 5. Side effects and the gate

Flight tools do not purchase tickets, send messages, or make reservations.
Any future tool with an external write effect must record a pending request
for a person to complete, following `CLAUDE.md`. SearchApi queries consume
billed searches. They may run unattended only under operator-configured request
and spending allowances: the user requests the search, inputs are validated,
and the server rejects requests when the allowance is exhausted. Do not treat
possession of an API key as permission for unlimited spending. No automatic
quota purchase or account upgrade is allowed. Cache writes support lookups and
do not authorize bookings.

## 6. Limits

The agent-loop intent sets an eight-step cap and a $0.05 USD model-cost cap
per run, with no overall wall-clock timeout. The loop stops at either cap.
A step is one model request, including a request that returns the final answer.
Add a maximum of 10 MCP tool-call attempts per run; count each call in a batch
separately, including failed calls. Check the allowance before dispatch, so a
batch cannot exceed the cap. These counts differ because one model response
can request multiple tools.

Maintain a separate SearchApi ledger: attempted upstream requests (including
retries and selection follow-ups), provider-confirmed billed searches when
available, cache hits, and estimated/confirmed cost. A tool-call cap alone is
not a search-spend cap. Enforce operator-configured per-tool and per-run
upstream request limits before each request; numeric defaults must be tested
against multi-city completion before final approval. Public hosting also needs
per-caller allowances so direct MCP clients cannot bypass loop limits.

The $0.05 allowance covers model use only. Before a model call, reserve its
maximum permitted token cost within the remaining allowance, then reconcile
with actual reported usage; do not describe checking after spending as a hard
cap. Refuse further calls if a safe bound cannot be established. SearchApi
charges are recorded separately and require their own configured allowance.
No overall run timeout is imposed, but each HTTP request must have a finite
timeout and bounded retries; exact values remain an implementation-plan decision.

## 7. Dependencies

| Dependency | Reason |
|---|---|
| `setuptools` (build only) | Approved by Gabriel to build the src-layout package for PyPI; exposes `fastflights-mcp` through the shared server entry point. Not a runtime service. |
| `mcp` | Official MCP server and protocol interfaces required by the lab; course example reports 2.3.0; verify and pin the working version during implementation. |
| `pytest` | Offline protocol tests covering schemas, validation, empty results, and simulated upstream failures. |
| `httpx` | HTTP client for SearchApi with async requests and explicit timeouts; preferred over stdlib `urllib` for integration with the async server. |
| `airportsdata` (pinned `==20260905`) | Maps an IATA airport code to its IANA timezone. SearchApi returns local clock times with no UTC offset, so the adapter needs it to attach offsets and compute real elapsed times. Approved by Gabriel; an older release is pinned because the newest needs Python 3.12 and this project supports 3.11. A code missing from the table makes the itinerary unusable; offsets are never guessed. Checked against the recorded fixtures: all 23 segments' computed durations match the provider's. |
| `tzdata` | Timezone database for `zoneinfo`, so offsets are identical on systems without an operating-system copy (Windows, slim containers, hosted deployment). Approved by Gabriel. |
| `pydantic` | Typed request/result validation and schema field constraints; explicit dependency because project code uses it directly. |
| Redis client (conditional on backend decision) | Install only if Redis is selected for final-release shared caching. In-memory cache is the alternative; neither Redis nor a cache service is required for Week 9 or offline CI. |
| OpenRouter SDK (package/version pending) | Agent-loop intent requires non-streaming model calls through OpenRouter. |

## 8. Cost estimate

SearchApi is selected. Record model and provider prices at evaluation time and
measure usage rather than inventing a total. Keep a running cost ledger in the
README with separate model-token charges and SearchApi search charges, plus
hosting/distribution costs when applicable. Record cap hits in evaluations.
The course brief asks for an explanation if the total exceeds about $10;
package publication and deployment still require their own operating estimates.

## 9. Decisions to resolve

- **Tool contracts:** review section 3 proposals, especially server-owned ranking,
  split infant counts, customization IDs, calendar limits, and
  exact-date historical comparisons. Verify pricing units and complete-trip
  provider mappings with fixtures before implementation.
- **Caching:** reconcile the newer airport intent selecting Redis with the
  spec, choose the client package, and define flight-cache TTLs and
  outage behavior. Offline tests use a fake cache and require no Redis service.
- **Loop:** evaluate the two model candidates recorded in the agent-loop intent;
  validate cost reservations and choose upstream request-cap defaults.
- **Remaining intent decisions:** resolve calendar window/trip-length limits and
  price-insight comparison behavior before finalizing those contracts.
- **Release operations:** Gabriel will select hosting, access controls, and
  publishing setup; both deployment and distribution remain required.
