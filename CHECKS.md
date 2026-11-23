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
