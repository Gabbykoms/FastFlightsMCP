# FastFlightsMCP: CHECKS.md

## Week 9

### Shared server scaffold

- Tests-first commit: `08e8276`; five collected cases initially errored because
  the application package did not exist.
- After adding the shared application and root launcher:
  `.venv/bin/python -m pytest tests/test_server_scaffold.py -q` — 5 passed.
- Verified in-memory MCP discovery, default stdio dispatch, loopback HTTP
  dispatch, root-launcher delegation, and rejection of unknown CLI arguments.
- No flight tools are registered yet. Transport dispatch tests mock startup;
  they do not prove live HTTP or subprocess connectivity.
- Gabriel approved setuptools as the build backend. Built the development wheel
  `fastflights_mcp-0.1.0.dev0-py3-none-any.whl` with `pip wheel . --no-deps`.
- Verified wheel contents and console entry point. Normal wheel installation,
  `fastflights-mcp --help`, and `python server.py --help` succeed.
- Re-ran all five scaffold tests against the installed package without a manual
  source-path override: 5 passed. No PyPI upload was performed.
- Editable installation built successfully, but this environment did not include
  the `.pth` source path at runtime. Use a normal installation here for now;
  editable-install compatibility remains to be investigated. This verification
  reused existing dependencies, not a fully clean external environment.

### `search_flights`: tests-first baseline (2026-11-22)

- Branch: `feat/search-flights`, based on merged specification PR #5.
- Environment: Python 3.15.2, MCP 2.3.0, pytest 9.2.0.
- Command: `.venv/bin/python -m pytest tests/test_search_flights.py -q --tb=short`.
- Result: 29 collected cases, 29 setup errors: `ModuleNotFoundError: No module
  named 'server'`. No server implementation exists yet; these are not passing
  tests or evidence of correct runtime behavior.
- Cases cover discovery/schema, strict wrong-type rejection with tool-body
  tracing, input bounds, cross-field validation, empty results, defaults,
  airport sets, sanitized timeout errors, budget selection, strict ceilings,
  the five-result limit, and exclusion of incomplete round trips.
- Tests forbid network access and remove the SearchApi key from the test
  environment. Candidate fixtures are synthetic normalized itineraries, not
  recordings of provider responses. Raw provider mapping and complete-return
  selection still need their own fixture tests.
- Proposed test seam: async `server.fetch_flight_candidates(**query)` returns
  normalized candidates. This is an internal testing boundary, not a new MCP tool.
- No paid calls, model calls, or Claude Code verification were performed.
- The remote `plan-week9` draft assigns this tool to Gabe, but its `Approved by`
  field was empty when inspected. Plan approval remains outstanding.

### `search_flights`: revised tests-first baseline

- Synced `feat/search-flights` with merged Week 9 plan and shared scaffold;
  preserved the original baseline below as historical evidence.
- Tests now import `fastflights_mcp.server`; root `server.py` stays a launcher.
  The normalized adapter seam is `fastflights_mcp.server.fetch_flight_candidates`:
  future registration code must expose/use this imported adapter reference.
  Tests temporarily inject an AsyncMock at this seam without implementing a tool.
  Provider response mapping remains a separate fixture-test responsibility.
- Every call first checks discovery so an unknown-tool error cannot pass an
  invalid-input test. Scaffold discovery permits future tool registration.
- Added complete round trips, response-envelope checks, 59/60/61-minute ranking,
  budget-slot reuse, no-within-budget recommendations, exact 20% limits,
  rejection of worse alternatives, nonstop filtering across budget groups,
  duration-versus-stop ranking, additional validation, and sanitized HTTP errors.
- Incomplete round-trip errors must carry `UPSTREAM_DATA_ERROR`; arbitrary
  errors no longer satisfy that case.
- Command: `PYTHONPATH=src .venv/bin/python -m pytest tests -q --tb=short`.
  Explicit source selection avoids testing a stale installed wheel.
- Actual result: **52 failed, 5 passed**. All flight cases fail at discovery
  because `search_flights` is not registered; there are no setup errors.
  Five scaffold cases pass. This is a failing baseline, not verified flight behavior.
- No network, paid provider calls, model calls, or tool implementation.
  `git diff --check` passed. Full local output: `/tmp/fastflights-revised-tests.log`.

### `search_flights`: implementation with placeholder adapter (2026-11-22)

- Branch: `feat/search-flights-impl`, based on `main` after PR #12 merged the tests.
- Commits: `992f57e` tool, `7f728ea` httpx dependency, `ddc5458` and `a5980d5`
  `client.py`, `f7d87e5` launcher and pytest path.
- Command: `python3 -m pytest tests -q`. Actual result: **57 passed** (52
  `search_flights` cases and 5 scaffold cases). Also **57 passed** with the editable
  install's `.pth` file deliberately hidden, so the run does not depend on it.
- One case initially failed: an unknown argument (`{"unknown": 1}`) was ignored by
  the SDK's generated argument model. Gabriel chose to close the model
  (`extra="forbid"` in `register()`); this uses SDK internals (`_tool_manager`).
- `fetch_flight_candidates` is a placeholder that raises `UPSTREAM_UNAVAILABLE`.
  All 57 results come from a faked adapter and synthetic candidates. No SearchApi
  or Google Flights call, no live data, no model, and no Claude Code check yet.
- Environment finding: the editable install's `.pth` file repeatedly gained the macOS
  `hidden` flag (`ls -lO`), and Python skips hidden `.pth` files
  (`python3 -v`: "Skipping hidden .pth file"), so `import fastflights_mcp` failed in
  pytest and the `client.py` subprocess. The cause is unknown (iCloud sync of
  `~/Desktop` is an unverified guess). The root `server.py` now puts `src/` on
  `sys.path` and pytest sets `pythonpath = ["src"]`. Revisit the cause if it matters.

### `search_flights`: SearchApi adapter (2026-11-23)

- Branch: `feat/search-flights-adapter-impl`, from `main` after PR #16 merged the failing tests
  (85 adapter cases in `tests/test_provider.py`, 4 children/infants cases in
  `tests/test_search_flights.py`). Implementation: `src/fastflights_mcp/provider.py`;
  `server.fetch_flight_candidates` delegates to it.
- Command: `python3 -m pytest tests -q`. Actual result: **147 passed** (57 existing, 4
  rejection, 86 adapter: the 85 from PR #16 plus one added here, see below). Before the
  implementation the same suite was 57 passed, 4 failed, 85 errors.
- All adapter tests use an `httpx.MockTransport` serving the sanitized recordings in
  `tests/fixtures/searchapi/`; no network and no key. Padded outbound lists, mutated options,
  shifted dates and the empty-result and error-key responses are synthetic.
- Mutation check: the adapter was deliberately broken in 8 ways (no party-total split, wrong
  time zone, key in the URL, client errors retried, no request cap, unusable data returned as
  an empty success, no timeout, no chronology check on the return leg). The tests caught 7.
  The eighth, the return-leg chronology check, had no adapter-level test, so
  `test_a_return_that_departs_before_the_outbound_lands_is_not_a_round_trip` was added;
  the break is now caught by exactly that test.
- Real stdio check: `env -u SEARCHAPI_API_KEY python3 client.py` exits 0. The valid
  call returns `CONFIGURATION_ERROR` with no network request, and `adults=10` is rejected by the
  schema. The tool description now says children and infants are not supported.
- Live check (Gabriel approved one request): `client.py` with the key forwarded made one real
  one-way search, JFK to LIS on 2027-01-22, economy, 1 adult, USD. The server log shows one
  `GET https://www.searchapi.io/api/v1/search?... "HTTP/1.1 200 OK"`. The tool returned 5
  itineraries with no error and no message; the recommended one is the nonstop TP 210 at
  404 USD, 2027-01-22T22:00:00-05:00 to 2027-01-23T10:05:00+00:00, 425 minutes. The others:
  DL 272 (527 USD, nonstop), AT 201 + AT 980 (521 USD, 1 stop), IB 212 + IB 529 (535 USD,
  1 stop) and FI 614 + FI 954 (502 USD, 1 stop, 2185 minutes, ranked last). The key value
  is absent from stdout and stderr. Cost at most $0.004.
- Finding: the MCP SDK's stdio client passes the server it launches only HOME, LOGNAME,
  PATH, SHELL, TERM and USER. `client.py` therefore reported `CONFIGURATION_ERROR` even with
  the key exported, until it was changed to forward `SEARCHAPI_API_KEY` explicitly.
  `plan.md` says the server inherits the key from the launching shell; that holds for
  nothing verified yet, and Claude Code's handling of `.mcp.json` environment variables
  must be checked in the registration step.
- **Not verified:** round trips, multiple airports and the error paths against the live
  provider (only a one-way search ran live); SearchApi's real "no flights" response is
  unrecorded; children and infants are rejected (stopgap) because the provider returns a
  party total; no Claude Code check yet.

### `search_airports`: API key kept out of URLs and logs (2026-11-23)

- Found while reviewing PR #11 (Nadia's tool, merged 2026-11-23): `_location_search` sent
  `SEARCHAPI_API_KEY` as the `api_key` query parameter. Checked offline with a synthetic key and an
  `httpx.MockTransport`: with the server's `logging.basicConfig(level=logging.INFO)`, httpx wrote the
  full URL, key included, to the log, and the `HTTPStatusError` text contained it too.
- Fix, tests first (`6a86889` failing, `d7730af` fix): the key now travels in an `Authorization: Bearer`
  header, and `main()` sets the `httpx` logger to WARNING so request URLs are not logged for any tool.
- Result: `python3 -m pytest tests -q`: **154 passed** (152 before, plus 2). Each fix was reverted in
  turn and exactly its own test failed.
- Not checked: no live `search_airports` request has been made (it is billed, and its response
  mapping is unrecorded). Other review findings on #11 are not addressed here: a blocking
  `httpx.get` inside an async tool, empty results cached, no stable error categories, and a cache
  with no expiry.

### `search_airports`: live failure in Claude Code, the `q` parameter and the real mapping (2026-11-23)

- In the first Claude Code session (plain `claude`, not `orclaude`; transcript in
  `transcripts/week09-claude-code-1-airport-lookup-failed.txt`, committed with the Week 9 PR) the model called `search_airports` 4 times and every call
  failed with "provider returned an error". It then used airport codes it already knew, and
  `search_flights` worked live for JFK, EWR and LGA to LIS. That also showed Claude Code passes the
  launching shell's `SEARCHAPI_API_KEY` to the server; `.mcp.json` has no `env` entry.
- Cause, from one diagnostic request: HTTP **400** `{"error": "Missing required parameter q."}`.
  `_location_search` sent the text as `query`. The tests replaced `_location_search` with a fake, so
  none could catch it. Failed requests are not billed.
- With `q` the response is HTTP 200, but it did not match the tool's mapping: places with nested
  `airports`, no `country`. The tool would have returned five blank matches as a success and
  cached them. Fixing `q` alone was therefore unsafe; the mapping was redone in the same change.
- Tests first (`4919994` and `53d70c9` failing, `3885cde` fix): `python3 -m pytest tests -q`:
  **163 passed** (154 on `main` plus 9). Six deliberate breaks (last place used, no country, duplicates
  kept, blank result on an unreadable shape, response shape unchecked, `query` again) were each caught.
- Recording: `tests/fixtures/searchapi/location_search_new_york.json`, one billed request.
- **Not verified:** airport-name queries (for example "Heathrow") are unrecorded; the fix has not been
  re-run through Claude Code; the other review findings on #11 (blocking `httpx.get`, error categories,
  cache expiry) are still open in issue #19.

### Tools
| Tool | Tests | What happened when Claude Code called it |
|---|---|---|
| | | |

### Output of `client.py`
Command: `SEARCHAPI_API_KEY=<set, not shown> python3 client.py`, run on 2026-11-23 at about
15:51 UTC. It starts `server.py` as a real stdio subprocess; no model is involved. The key is
forwarded to the server explicitly (see the adapter entry above). Exit 0. The first call is a
**real, billed SearchApi request** (one one-way search, JFK to LIS on 2027-01-22); the second
call (`adults=10`) is rejected by the schema before any request. The server's own logs went
to stderr and stdout stayed clean. stderr held two lines: the `HTTP/1.1 200 OK` for the search,
and the rejected `adults` argument. The key value appears in neither. Earlier
versions of this block (placeholder adapter, then the key-unset `CONFIGURATION_ERROR` run) are
in git history.
```text

search_flights: Find complete one-way or round-trip flights once locations, dates and cabin are known.

Call this after city names are resolved to IATA airport codes (use search_airports).
Omit return_date for one-way. Returns up to five itineraries and one recommendation.
An empty result is a normal answer; ask the traveler before changing the budget,
dates or airports. Prices and budgets are per person for the whole itinerary.
Children and infants are not supported yet: the party must be adults only.

  schema: {"origin": {"description": "Distinct uppercase IATA airport codes, 1 to 10.", "items": {"pattern": "^[A-Z]{3}$", "type": "string"}, "maxItems": 10, "minItems": 1, "title": "Origin", "type": "array"}, "destination": {"description": "Distinct uppercase IATA airport codes, 1 to 10.", "items": {"pattern": "^[A-Z]{3}$", "type": "string"}, "maxItems": 10, "minItems": 1, "title": "Destination", "type": "array"}, "depart_date": {"pattern": "^\\d{4}-\\d{2}-\\d{2}$", "title": "Depart Date", "type": "string"}, "cabin": {"enum": ["economy", "premium_economy", "business", "first"], "title": "Cabin", "type": "string"}, "return_date": {"anyOf": [{"pattern": "^\\d{4}-\\d{2}-\\d{2}$", "type": "string"}, {"type": "null"}], "default": null, "title": "Return Date"}, "adults": {"default": 1, "maximum": 9, "minimum": 1, "title": "Adults", "type": "integer"}, "children": {"default": 0, "maximum": 8, "minimum": 0, "title": "Children", "type": "integer"}, "infants_in_seat": {"default": 0, "maximum": 8, "minimum": 0, "title": "Infants In Seat", "type": "integer"}, "infants_on_lap": {"default": 0, "maximum": 8, "minimum": 0, "title": "Infants On Lap", "type": "integer"}, "currency": {"default": "USD", "pattern": "^[A-Z]{3}$", "title": "Currency", "type": "string"}, "budget": {"anyOf": [{"exclusiveMinimum": 0, "type": "number"}, {"type": "null"}], "default": null, "description": "Per person, whole trip.", "title": "Budget"}, "strict_budget": {"default": false, "title": "Strict Budget", "type": "boolean"}, "max_stops": {"anyOf": [{"maximum": 2, "minimum": 0, "type": "integer"}, {"type": "null"}], "default": null, "description": "Per requested leg.", "title": "Max Stops"}}

--- search_flights, valid one-way request
is_error: False | {
  "query": {
    "origin": [
      "JFK"
    ],
    "destination": [
      "LIS"
    ],
    "depart_date": "2027-01-22",
    "return_date": null,
    "cabin": "economy",
    "adults": 1,
    "children": 0,
    "infants_in_seat": 0,
    "infants_on_lap": 0,
    "currency": "USD",
    "budget": null,
    "strict_budget": false,
    "max_stops": null
  },
  "fetched_at": "2026-11-23T15:51:03.715438Z",
  "cache_hit": false,
  "message": null,
  "results": [
    {
      "id": "fl_a1f57c7e9d85fbf8",
      "legs": [
        {
          "origin": "JFK",
          "destination": "LIS",
          "departure": "2027-01-22T22:00:00-05:00",
          "arrival": "2027-01-23T10:05:00+00:00",
          "duration_minutes": 425,
          "stops": 0,
          "segments": [
            {
              "airline": "Tap Air Portugal",
              "flight_number": "TP 210",
              "origin": "JFK",
              "destination": "LIS",
              "departure": "2027-01-22T22:00:00-05:00",
              "arrival": "2027-01-23T10:05:00+00:00",
              "duration_minutes": 425
            }
          ],
          "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD"
        }
      ],
      "price_per_person": 404.0,
      "currency": "USD",
      "total_travel_minutes": 425,
      "total_stops": 0,
      "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD",
      "separate_tickets": null,
      "budget_status": "not_specified",
      "budget_overage": null,
      "recommendation_reasons": [
        "Nonstop",
        "Total travel time 7h 05m",
        "404 USD per person"
      ]
    },
    {
      "id": "fl_186da4b8831551ab",
      "legs": [
        {
          "origin": "JFK",
          "destination": "LIS",
          "departure": "2027-01-22T20:20:00-05:00",
          "arrival": "2027-01-23T08:00:00+00:00",
          "duration_minutes": 400,
          "stops": 0,
          "segments": [
            {
              "airline": "Delta",
              "flight_number": "DL 272",
              "origin": "JFK",
              "destination": "LIS",
              "departure": "2027-01-22T20:20:00-05:00",
              "arrival": "2027-01-23T08:00:00+00:00",
              "duration_minutes": 400
            }
          ],
          "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD"
        }
      ],
      "price_per_person": 527.0,
      "currency": "USD",
      "total_travel_minutes": 400,
      "total_stops": 0,
      "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD",
      "separate_tickets": null,
      "budget_status": "not_specified",
      "budget_overage": null,
      "recommendation_reasons": []
    },
    {
      "id": "fl_c8d474ac2d514a0d",
      "legs": [
        {
          "origin": "JFK",
          "destination": "LIS",
          "departure": "2027-01-22T20:00:00-05:00",
          "arrival": "2027-01-23T11:35:00+00:00",
          "duration_minutes": 635,
          "stops": 1,
          "segments": [
            {
              "airline": "Royal Air Maroc",
              "flight_number": "AT 201",
              "origin": "JFK",
              "destination": "CMN",
              "departure": "2027-01-22T20:00:00-05:00",
              "arrival": "2027-01-23T07:50:00+00:00",
              "duration_minutes": 410
            },
            {
              "airline": "Royal Air Maroc",
              "flight_number": "AT 980",
              "origin": "CMN",
              "destination": "LIS",
              "departure": "2027-01-23T10:05:00+00:00",
              "arrival": "2027-01-23T11:35:00+00:00",
              "duration_minutes": 90
            }
          ],
          "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD"
        }
      ],
      "price_per_person": 521.0,
      "currency": "USD",
      "total_travel_minutes": 635,
      "total_stops": 1,
      "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD",
      "separate_tickets": null,
      "budget_status": "not_specified",
      "budget_overage": null,
      "recommendation_reasons": []
    },
    {
      "id": "fl_c1bdcd09712246e8",
      "legs": [
        {
          "origin": "JFK",
          "destination": "LIS",
          "departure": "2027-01-22T17:05:00-05:00",
          "arrival": "2027-01-23T07:40:00+00:00",
          "duration_minutes": 575,
          "stops": 1,
          "segments": [
            {
              "airline": "Iberia",
              "flight_number": "IB 212",
              "origin": "JFK",
              "destination": "MAD",
              "departure": "2027-01-22T17:05:00-05:00",
              "arrival": "2027-01-23T06:20:00+01:00",
              "duration_minutes": 435
            },
            {
              "airline": "Iberia",
              "flight_number": "IB 529",
              "origin": "MAD",
              "destination": "LIS",
              "departure": "2027-01-23T07:15:00+01:00",
              "arrival": "2027-01-23T07:40:00+00:00",
              "duration_minutes": 85
            }
          ],
          "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD"
        }
      ],
      "price_per_person": 535.0,
      "currency": "USD",
      "total_travel_minutes": 575,
      "total_stops": 1,
      "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD",
      "separate_tickets": null,
      "budget_status": "not_specified",
      "budget_overage": null,
      "recommendation_reasons": []
    },
    {
      "id": "fl_381c405d9ed66732",
      "legs": [
        {
          "origin": "JFK",
          "destination": "LIS",
          "departure": "2027-01-22T19:25:00-05:00",
          "arrival": "2027-01-24T12:50:00+00:00",
          "duration_minutes": 2185,
          "stops": 1,
          "segments": [
            {
              "airline": "Icelandair",
              "flight_number": "FI 614",
              "origin": "JFK",
              "destination": "KEF",
              "departure": "2027-01-22T19:25:00-05:00",
              "arrival": "2027-01-23T06:10:00+00:00",
              "duration_minutes": 345
            },
            {
              "airline": "Icelandair",
              "flight_number": "FI 954",
              "origin": "KEF",
              "destination": "LIS",
              "departure": "2027-01-24T08:30:00+00:00",
              "arrival": "2027-01-24T12:50:00+00:00",
              "duration_minutes": 260
            }
          ],
          "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD"
        }
      ],
      "price_per_person": 502.0,
      "currency": "USD",
      "total_travel_minutes": 2185,
      "total_stops": 1,
      "google_flights_url": "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI3LTAxLTIyagcIARIDSkZLcgcIARIDTElTQAFIAXABggELCP___________wGYAQI&hl=en&gl=us&curr=USD",
      "separate_tickets": null,
      "budget_status": "not_specified",
      "budget_overage": null,
      "recommendation_reasons": []
    }
  ],
  "recommended_id": "fl_a1f57c7e9d85fbf8",
  "within_budget_found": null
}

--- search_flights with adults=10 (the schema says 1 to 9)
is_error: True | Error executing tool search_flights: 1 validation error for search_flightsArguments
adults
  Input should be less than or equal to 9 [type=less_than_equal, input_value=10, input_type=int]
    For further information visit https://errors.pydantic.dev/2.14/v/less_than_equal
```

### The stray `print` error

Experiment (2026-11-23, Python 3.12.4, mcp 2.3.0): added `print("hello")` as the
first line of the `search_flights` tool body, ran `.venv/bin/python client.py`,
then removed the `print`. The client logged this on stderr when the tool ran:

```text
Failed to parse JSONRPC message from server
Traceback (most recent call last):
  File ".../site-packages/mcp/client/stdio.py", line 222, in _parse_line
    message = types.jsonrpc_message_adapter.validate_json(line, by_name=False)
pydantic_core._pydantic_core.ValidationError: 1 validation error for union[JSONRPCRequest,JSONRPCNotification,JSONRPCResponse,JSONRPCError]
  Invalid JSON: expected value at line 1 column 1 [type=json_invalid, input_value='hello', input_type=str]
```

Why: over stdio the server's stdout *is* the protocol channel, so the host tries to
parse every line it receives as a JSON-RPC message, and `hello` is not one. In this
SDK version the client logged the bad line and skipped it, so `client.py` still
exited 0 and both calls completed; a host is not required to be that forgiving, and
any output that happened to look like a partial message could corrupt the stream.
This is why the server logs with `logging` (stderr) and prints nothing.

### Server or skill?

This task needs a server. The tools wrap an external, billed system (SearchApi) that
needs a credential, and the server keeps `SEARCHAPI_API_KEY` in its own environment
so neither host nor model ever sees it. Team Project 2 also requires the same tools
in two hosts (Claude Code and our own agent loop); a server lets both discover and
call identical tools and schemas without sharing code. One piece could have been a
skill or script instead: `search_airports` is a single lookup that turns a city name
into airport codes, and if the only host were a coding agent with a shell, a skill
running a short script would do the job. We kept it in the server because our own
loop has no shell or skills, both hosts need it, and it shares the server's
credential, error categories, and planned caching.

### The gate

No Week 9 tool changes anything: `search_flights` and `search_airports` only read
search results. Nothing is booked, purchased, sent, or saved for later, so there is
no pending request to record yet. The cost of each search is the one real-world
effect. Invalid requests are rejected before any upstream call, and in the agent
loop the step cap limits how many tool calls a run can make; the $0.05 cap covers
model tokens only, and a cap on billed searches is still open in `spec.md`.

None of the tools planned for Team Project 2 has a side effect either; the spec
excludes booking and payment. The first tool that would need the gate is a
price-watch alert, currently deferred in `spec.md` section 1. If the team adds it,
the server would only append a pending alert request (route, dates, target price,
contact) to a local file and return `pending: true`; it would never send a message
or subscribe anyone on the model's say-so. The host's permission prompt is a second
check, and a person would review the pending file and decide whether to enable the
alert.
