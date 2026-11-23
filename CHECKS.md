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
- **Not verified:** no live SearchApi request was made through the adapter. Real responses
  are covered only by the recordings (one route, one date). Children and infants are
  rejected (stopgap) because the provider returns a party total; SearchApi's real "no
  flights" response is unrecorded; no Claude Code check yet.

### Tools
| Tool | Tests | What happened when Claude Code called it |
|---|---|---|
| | | |

### Output of `client.py`
Command: `python3 client.py` (real stdio subprocess, no model, no key, no network).
Exit 0; stdout shown, server logs went to stderr. The valid call ends in the
placeholder adapter's error, which is expected until the real adapter exists.
```text

search_flights: Find complete one-way or round-trip flights once locations, dates and cabin are known.

Call this after city names are resolved to IATA airport codes (use search_airports).
Omit return_date for one-way. Returns up to five itineraries and one recommendation.
An empty result is a normal answer; ask the traveler before changing the budget,
dates or airports. Prices and budgets are per person for the whole itinerary.

  schema: {"origin": {"description": "Distinct uppercase IATA airport codes, 1 to 10.", "items": {"pattern": "^[A-Z]{3}$", "type": "string"}, "maxItems": 10, "minItems": 1, "title": "Origin", "type": "array"}, "destination": {"description": "Distinct uppercase IATA airport codes, 1 to 10.", "items": {"pattern": "^[A-Z]{3}$", "type": "string"}, "maxItems": 10, "minItems": 1, "title": "Destination", "type": "array"}, "depart_date": {"pattern": "^\\d{4}-\\d{2}-\\d{2}$", "title": "Depart Date", "type": "string"}, "cabin": {"enum": ["economy", "premium_economy", "business", "first"], "title": "Cabin", "type": "string"}, "return_date": {"anyOf": [{"pattern": "^\\d{4}-\\d{2}-\\d{2}$", "type": "string"}, {"type": "null"}], "default": null, "title": "Return Date"}, "adults": {"default": 1, "maximum": 9, "minimum": 1, "title": "Adults", "type": "integer"}, "children": {"default": 0, "maximum": 8, "minimum": 0, "title": "Children", "type": "integer"}, "infants_in_seat": {"default": 0, "maximum": 8, "minimum": 0, "title": "Infants In Seat", "type": "integer"}, "infants_on_lap": {"default": 0, "maximum": 8, "minimum": 0, "title": "Infants On Lap", "type": "integer"}, "currency": {"default": "USD", "pattern": "^[A-Z]{3}$", "title": "Currency", "type": "string"}, "budget": {"anyOf": [{"exclusiveMinimum": 0, "type": "number"}, {"type": "null"}], "default": null, "description": "Per person, whole trip.", "title": "Budget"}, "strict_budget": {"default": false, "title": "Strict Budget", "type": "boolean"}, "max_stops": {"anyOf": [{"maximum": 2, "minimum": 0, "type": "integer"}, {"type": "null"}], "default": null, "description": "Per requested leg.", "title": "Max Stops"}}

--- search_flights, valid one-way request
is_error: True | Error executing tool search_flights: UPSTREAM_UNAVAILABLE: The SearchApi adapter is not implemented in this build.

--- search_flights with adults=10 (the schema says 1 to 9)
is_error: True | Error executing tool search_flights: 1 validation error for search_flightsArguments
adults
  Input should be less than or equal to 9 [type=less_than_equal, input_value=10, input_type=int]
    For further information visit https://errors.pydantic.dev/2.14/v/less_than_equal
```

### The stray `print` error
```text
```

### Server or skill?
<!-- One paragraph: why this task needs a server, and one piece of it that could have
     been a script or a skill instead, with the reason for the choice. -->

### The gate
<!-- For the tool that changes something: what the server does, what the host did, and
     what a person still has to do. If there is no such tool yet, which TP2 tool is first. -->
