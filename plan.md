# Plan

## Week 9: the server

### Files to create or change
| File | Purpose |
|---|---|
| `server.py` | MCP server using `mcp.server.MCPServer` (SDK v2). Holds the tool functions, the `MCPServer` instance, and `if __name__ == "__main__": mcp.run()`. Reads `SEARCHAPI_API_KEY` from the environment; never returns it. Nothing printed to stdout; use `logging`. |
| `client.py` | Host with no model. Calls each registered tool once via an in-memory `Client`, prints what a real host receives (the schemas and results), and exercises the stdio path so a stray `print` shows up as a protocol error. |
| `tests/` | Offline protocol tests per tool. Each test file opens the server with `async with Client(mcp)`. SearchApi and the Redis cache are fakes; no key, no network, no Redis. |
| `.mcp.json` | Registers the server in Claude Code under a team name, command `python3`, args `["server.py"]`. Committed so every teammate gets it on `git pull`; any secrets live in the user's local env, not in the file. |

### First tools
`search_airports` and `search_flights` — both read-only, no side-effect tool yet.
`search_airports` resolves a city or query to IATA codes (the airport lookup intent).
`search_flights` returns up to five eligible itineraries with one recommendation
(spec section 3.3, search-flights intent). Both run on SearchApi; both honor the
shared error categories and never expose the API key.

### Order of work
For each tool, in order:

1. Write tests in `tests/test_<name>.py` covering the five misuse cases in
   lab step 3. Run them so they fail. Commit: `Server: tests for <name>, failing`.
2. Implement the tool in `server.py` so the tests pass. Schema from type hints
   (`Annotated` + `Field`), `ToolError` for failures the model should see.
   Rewrite the docstring until it says **when** to call the tool. Commit:
   `Server: <name>`.
3. Update `client.py` to call the new tool once; paste the printed schemas
   into `CHECKS.md`.
4. With the tool in `.mcp.json` and the venv active, start `claude`, approve
   the server, run `/mcp`, ask a question that needs the tool, and export
   the transcript under `transcripts/`.

Cross-cutting, once: `.mcp.json` is added when the first tool is wired so
teammates can run `/mcp` while the second tool lands. When more than one person
edits `server.py`, pull before starting a tool and commit small to avoid merge
conflicts.

### Who is doing what
| Member | Work |
|---|---|
| Nadia | `search_airports` (Week 9); agent loop (later week). |
| Gabe | `search_flights` (Week 9); `search_multi_city` (later week). |
| Patrick | `find_cheapest_dates` and `get_price_insight` (later weeks). |

Every member can explain every tool, per the TP2 sheet.

### Risks
- **Merge conflicts in `server.py`** when both Week-9 owners add tools in the
  same week. Mitigation: pull before starting a tool, commit small per step.
- **Docstrings are the only thing the model reads** to decide when to call the
  tool. A docstring that says only what the tool is will be ignored. The owner
  rewrites theirs until it names the situation that should trigger a call.
- **Stdout contamination**: any `print` in `server.py` corrupts the JSON-RPC
  stream and breaks the host. Tests must fail on stray prints; `logging` only.
- **SearchApi billing**: a too-wide date window or airport set on
  `search_flights` could consume many billed searches before the loop's
  $0.05 cost cap or the eight-step cap stops anything. Strict preflight
  validation (date order, party size, airport set size) before any upstream
  request; final per-call request caps decided before implementation.
- **Redis is not running**: the airport intent (PR #7) decides this — the
  server logs a warning and serves lookups without the cache; tests use a
  fake cache and need neither Redis nor an API key. Cached lookups stay
  valid for 30 days. No runtime change needed in Week 9 beyond logging.
- **Wrong-type inputs must not run the function body**: a fast error before
  any upstream call is also what protects the SearchApi quota from misuse.

### Proof
For each Week 9 tool, its no-key tests cover the five misuse cases from lab step 3:

**`search_airports`** tests cover:
1. Tool is listed; schema matches spec (`query`: string, 1–100 chars after trim).
2. Wrong type → `is_error` result, function body did not run, no upstream call.
3. Whitespace-only or out-of-range `query` → `is_error` result.
4. No match upstream → normal success with `matches: []` and an explanatory message.
5. Upstream failure (HTTP error, missing key, quota) faked → `ToolError` with an
   actionable message; the key is not in the message.

**`search_flights`** tests cover:
1. Tool is listed; schema matches spec (`origin`/`destination` AirportSet,
   `depart_date` Date, `cabin` enum, passenger args, optional `return_date`,
   `budget`, `strict_budget`, `max_stops`).
2. Wrong type on any required field → `is_error` result, function body did not run.
3. Out-of-range or cross-field invalid (e.g. `return_date` before `depart_date`,
   passenger sum over 9, infants_on_lap > adults) → `is_error` result.
4. No eligible complete itinerary → normal success with `results: []`,
   `recommended_id: null`, and a message identifying the absence.
5. Upstream failure faked → `ToolError` with an actionable category-stable
   message; key and authenticated URL not in the message.

What registering the server in Claude Code proves that the tests do not:
- The server boots under `python3 server.py` from a teammate's venv — tests
  only construct the `MCPServer` object and never run the stdio entry point.
- The schemas Claude Code publishes actually let a model pick and call the
  tools in response to a question — tests check what schemas say, not whether
  they persuade a model.
- The server stays silent on stdout when started as a real subprocess — an
  in-memory client can miss prints that a real host would surface as
  "Failed to parse JSONRPC message."

### Questions addressed during plan review

1. **Wrong-type input to `search_airports` or `search_flights`: what comes
   back, and does our function run?** The mcp SDK validates the call against
   the tool's schema before invoking the function. The host gets an MCP
   error result with a field-specific correction (`is_error` true, per spec
   3.1: "schema rejection is an MCP error result with a field-specific
   correction"). The function body does not run, so no upstream SearchApi
   request is made — that also protects the quota. Tests in Proof items 2
   and 3 for each tool already cover this: `is_error` true and the fake
   upstream not called.

2. **Where does `SEARCHAPI_API_KEY` live, and what would have to go wrong
   for the model to see it?** It lives in the server's process environment:
   set in the shell that launches the host. Claude Code starts
   the server as a subprocess, so the server inherits it from that shell.
   `.mcp.json` is committed, so it must never contain the value; if an
   `env` block is needed, it references `${SEARCHAPI_API_KEY}` only. It is never in
   source. For the model to see it, the value would have to be returned in
   a tool result, an error message, or a log line. Spec 3.1 forbids
   un-sanitized provider error text, and the upstream-failure tests in
   Proof item 5 for each tool assert the key is absent from the `ToolError`
   message. Realistic leak paths the tests already guard against: a debug
   `print(os.environ)` to stdout (also breaks the JSON-RPC stream), an
   authenticated URL echoed in an error message, or a logged request URL.
   Proof items 5 already cover the message-leak path; a stray-print check
   is added to `client.py` (lab step 5) rather than as a separate test.

**What changed in the plan:**
- Risks: replaced the "Redis unavailability (open per intent)" bullet with
  the decided policy from PR #7 — server logs a warning and serves without
  the cache; tests fake the cache and need neither Redis nor an API key;
  cached lookups stay valid for 30 days. No longer listed as open.
- Proof: already covered wrong-type rejection and key-leakage in upstream
  failure; no change.
- Answer 2 corrected by hand: the agent first suggested putting the key in the env block of .mcp.json, but that file is committed. The key comes from the launching shell; .mcp.json may only reference ${SEARCHAPI_API_KEY}.

**Approved by:**
