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

### Tools
| Tool | Tests | What happened when Claude Code called it |
|---|---|---|
| | | |

### Output of `client.py`
```text
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
