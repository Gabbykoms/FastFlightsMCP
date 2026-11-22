# FastFlightsMCP: CHECKS.md

## Week 9

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
