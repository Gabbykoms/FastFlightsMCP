# Full implementation and release plan

Status: Full scope accepted by Gabriel and Nadia; Week 9 tool implementation and verification pending.
Scope direction: Gabriel requested all tools and release features in this implementation.
This replaces the two-tool-only plan. The Week 9 lab remains a verification
checkpoint within the work, not the definition of completion.

## Deliverables

- All five tools: `search_airports`, `search_flights`, `search_multi_city`,
  `find_cheapest_dates`, and `get_price_insight`.
- Shared validated contracts, real SearchApi integration, ranking, budget
  selection, complete-itinerary handling, and per-leg customization.
- Caching, request limits, sanitized errors, and credential isolation.
- Claude Code and our own agent loop completing the same multi-step task.
- Offline protocol and adapter tests, two-model evaluation, CI, and cost evidence.
- Public streamable HTTP deployment and a published PyPI package usable through
  `uvx fastflights-mcp`, both verified from external clients/installations.
- Installation documentation, transcripts, annotations, and submission artifacts.

Price-watch alerts remain explicitly deferred. Baggage/fare-class details remain
an unresolved product decision in the spec; this plan does not claim they are
implemented or silently defer them. Settle that scope before declaring completion.

## Ownership and integration

Tool ownership follows the team's remote plan. Gabriel has confirmed ownership
of the shared scaffold, integration, CI, and submission coordination as well as
deployment and distribution. Nadia has accepted cache integration and model evaluation. Patrick
has confirmed ownership of the calendar and price-insight provider fixtures.

| Owner | Work |
|---|---|
| Gabriel | `search_flights`, `search_multi_city`, shared server/package scaffold, integration, CI, submission coordination, hosted deployment, and PyPI/uvx distribution. Ownership confirmed. |
| Nadia | `search_airports` and the agent loop. Cache integration and model evaluation ownership accepted. |
| Patrick | `find_cheapest_dates` and `get_price_insight`. Calendar/insight provider-fixture ownership confirmed. |
| All members | Review contracts and docstrings, run local host verification, review PRs, and explain the whole system. Gabriel coordinates collection of evidence and submission artifacts. |

Create one shared server instance and one provider interface first. Tool modules
register against that instance; do not create competing servers. Keep a root
`server.py` launcher for the lab and route it and the published console entry
point to the same package implementation. Import any existing teammate scaffold
before creating a duplicate. Keep tests-first commits separate from implementation
commits, and integrate reviewed branches without overwriting teammates' work.

## Files and components

| Files | Purpose |
|---|---|
| `server.py`, `src/fastflights_mcp/server.py` | Root launcher, MCP instance, registration, stdio and HTTP startup. |
| `src/fastflights_mcp/tools/` | One module per tool using the section 3 contracts in `spec.md`. |
| `src/fastflights_mcp/models.py` | Strict inputs, cross-field validation, normalized outputs. |
| `src/fastflights_mcp/provider.py` | Shared `httpx` client, SearchApi mapping, selection follow-ups, timeouts, retries, and usage accounting. |
| `src/fastflights_mcp/cache.py` | Query normalization, caching, expiry, and cache-outage policy. |
| `agent.py` | OpenRouter loop, MCP discovery/dispatch, step and cost caps, transcript and ledger output. |
| `client.py`, `.mcp.json` | No-model verification client and shared Claude Code registration. |
| `tests/`, `tests/fixtures/` | Offline MCP and adapter tests; synthetic data clearly distinguished from sanitized real recordings. |
| `pyproject.toml`, `requirements.txt` | Package metadata, entry point, supported Python versions, dependencies and test setup. |
| `.github/workflows/` | Offline CI plus controlled billed evaluation and release workflows. Never expose credentials to untrusted PR jobs. |
| `evals/`, `transcripts/` | Evaluation tasks/results and representative host transcripts with secrets removed. |
| Deployment configuration | Platform-specific startup, credentials, access control, rate/spending limits, health checks, and rollback instructions. Platform choice precedes creating its files. |
| `README.md`, `spec.md`, `CHECKS.md`, `ANNOTATION.md`, `CLAUDE.md` | Contracts, setup, costs, verification evidence, lessons, and current working rules. |
| `REVIEW.md`, reusable workflow skill | Course review artifact and packaged reusable workflow, following the brief. |

## Order of work

These are stages inside one full implementation milestone. Finishing stage 2
alone does not complete the requested work.

### 1. Align contracts and establish the shared foundation

- Reconcile the newer airport intent with the spec: it selects Redis, a 30-day
  airport TTL, and warning plus uncached fallback when Redis is unavailable.
  Confirm the dependency and preserve fake-cache tests without a Redis service.
- Resolve remaining implementation choices: upstream request limits, timeout and
  retry values, calendar limits, mixed-party price basis, timestamp offsets,
  flight-cache/selection expiry, and unsupported-filter behavior. Record decisions
  in the spec before implementing dependent behavior.
- Verify MCP SDK interfaces and dependency versions against the course example;
  create the shared package/server scaffold, environment configuration, and
  provider interface. Add `httpx` and `pydantic` as explicit dependencies.
- Capture approved, bounded SearchApi responses with credentials removed to prove
  airport fields, per-person pricing, complete round-trip/multi-city selection,
  calendar coverage, and price history. Record any billed requests in the ledger.
  Do not label synthetic fixtures as provider verification.

### 2. Implement every tool, tests first

For each tool: write offline protocol and mapping tests, run and commit the
failing baseline, implement the tool, run the applicable checks, and commit the
passing implementation separately. A named reviewer checks its call guidance.

1. `search_airports`: query validation, all relevant airport matches, API-order
   preservation, empty results, Redis caching/fallback, and sanitized errors.
2. `search_flights`: one-way and complete round trips, airport sets, passenger
   and cabin handling, hard filters, chronological feasibility, ranking,
   five-result allocation, inclusive budget allowance, and strict ceilings.
3. `search_multi_city`: 2–6 legs, same-city continuity, the 24-hour airport-change
   rule, complete-trip prices, scoped leg filters, and customization holding
   other selected flights fixed. Expired selections fail explicitly.
4. `find_cheapest_dates`: one-way and round-trip windows, fixed-stay handling,
   preflight combination limits, cheapest-date ordering, tied minima, and
   explicit unavailable dates. Never invent missing prices or calendar coverage.
5. `get_price_insight`: exact trip scope, provider price signal and history,
   requested comparison dates, unavailable observations, and no invented forecasts.

Steps 3–5 can proceed once their shared dependencies are ready. They are required
in this milestone, not future-roadmap items. The current `search_flights` test
commit is a starting baseline, not proof of a working tool or adapter.

### 3. Complete cross-tool behavior and both hosts

- Implement cache keys including every price-affecting input and credential/tenant
  boundaries; preserve original fetch timestamps. Reuse equivalent flight data
  for insight queries without re-billing unnecessarily.
- Enforce server-side upstream limits before requests, including retries and
  selection follow-ups. A host's model budget does not protect provider spend.
- Verify no-model schema/result discovery and the real stdio process path.
  Temporarily introduce the lab's stdout error, record it, and remove it.
- Register all five tools in Claude Code. Every teammate verifies locally; commit
  a representative sanitized task transcript and observed failures.
- Implement `agent.py`: one model per run, eight model steps, ten tool-call
  attempts, the specified model-cost allowance, separate SearchApi accounting,
  and useful failure output when required prompt information is missing.
- Demonstrate the same airport/date/flight task in both hosts. Verify multi-city
  and insight workflows too; registering a tool is not evidence it works.

### 4. Evaluation, CI, and evidence

Gabriel owns CI and submission coordination, including collecting verification
evidence, checking required artifacts, and coordinating submission tags and the
team submission. Individual members remain responsible for their assigned work
and local verification. Nadia has accepted model-evaluation ownership.

- Compare the two model candidates in the spec on identical tasks, constraints,
  and caps. Record correctness, invalid calls, cap hits, latency, and billed cost;
  choose the final model using the measured results.
- Keep default CI offline with fake HTTP/cache interfaces and no credentials.
  Add a bounded, authorized billed-eval job with separate secrets and cost records.
- Complete `CHECKS.md`, component annotations, and the README cost ledger. Capture
  actual outputs; do not fill evidence sections with planned or assumed results.
- Complete reviewed PRs and course-required review/skill artifacts. Record who
  owns each submission artifact rather than assuming the tool owner does it.

### 5. Deploy and distribute (Gabriel)

- Choose the host platform and credential model; configure authentication,
  tenant/session isolation, HTTP timeouts, per-caller quotas and spending caps,
  logging without secrets, health checks, and rollback procedures.
- Deploy the same server implementation over streamable HTTP. Verify discovery
  and calls from an external compatible client, authorization failures, quota
  exhaustion, and upstream failures. Do not declare deployment complete from a
  localhost-only check.
- Build wheel and source distribution, check package contents/dependencies and
  entry point, test clean installation and stdio startup, then publish to PyPI.
- Verify `uvx fastflights-mcp` against the published version from a clean
  environment; document credentials, Redis requirements/fallback, supported
  hosts, limitations, and remote connection instructions.
- Package availability and hosted access must both work before completion.

## Proof and acceptance checks

Every tool requires schema discovery, wrong-type rejection before tool-body
execution, bounds/cross-field checks before upstream calls, a normal empty or
unavailable result, and simulated operational failures with actionable errors.
Offline tests use in-memory `Client(mcp)`, no model, no network, and no subprocess.
Separate integration/release checks exercise real process and transport behavior.

| Area | Additional required evidence |
|---|---|
| Airports | Relevant airport sets, explicit airport preservation, 10-result cap, cache hit/expiry/outage behavior, real response mapping. |
| Flights | Return-flight completion and actual total fare; mixed-party price basis; airport combinations; ranking and one-hour tie handling; 3+2 allocation and unused slots; 20% inclusive boundary; strict budget and hard filters. |
| Multi-city | All legs present; city continuity; 23h59m versus 24h airport-change boundary; elapsed-time correctness; fixed-other-leg customization; stale IDs; no partial trips. |
| Calendar | Inclusive window boundaries and combination cap before requests; fixed nights; no-flight dates versus incomplete data; cheapest ties and requested currency. |
| Insight | Provider signal unchanged, exact historical dates, missing history/insight, no forecasts, cached reuse, failure distinct from absent data. |
| Shared server | Sanitized errors/logs, absent credentials, malformed provider data, request/time/retry limits, schema and stdio checks, tenant isolation for hosted selections. |
| Hosts and eval | Same task in both hosts, all tool workflows exercised, cost/step exhaustion, measured two-model comparison, transcript evidence. |
| Release | Clean published-package installation, external hosted-client use, access control and quota tests, documented rollback and setup. |

## Risks and controls

- Shared-file conflicts: one scaffold owner, modules per tool, small reviewed commits.
- Incomplete provider responses: fixture-based mapping and complete-trip assertions;
  never represent a selected outbound leg as a priced round trip.
- Billing escalation: enforce request allowances before calls; count every retry
  and selection request; reconcile estimates against provider billing records.
- Price or time ambiguity: do not guess units or offsets; settle mappings before
  recommending affected results.
- Cache outages: implement and test the decided fallback; no Redis requirement
  for offline tests. Do not confuse process-local caching with shared caching.
- Model unreliability: strict server validation, bounded loop, reproducible eval.
- Deployment/distribution failure: test the actual published artifact and external
  service, not only development installs and localhost.
- Scope mismatch: all five tools and release work are mandatory; unresolved
  contract choices remain visible rather than being labeled completed.

## Questions addressed during plan review

1. **What happens when a host sends a wrong type?** Schema validation returns an
   MCP error before the tool body executes. Tests trace body entry and assert
   no upstream call. Cross-field failures also stop before paid requests.
2. **Where do credentials live?** SearchApi credentials live in the server
   environment; OpenRouter credentials live in the loop environment. No key in
   committed `.mcp.json`, logs, fixtures, results, or transcripts. Configure
   inherited environment variables or the platform secret store.
3. **How is paid search bounded?** It is not free merely because it is read-only.
   Server-side allowances gate each provider request; model cost and provider
   cost are separate. No quota purchase or upgrade happens automatically.
4. **What does host registration prove beyond unit tests?** Real interpreter,
   launch environment, stdio transport, tool discovery by a model, and successful
   task execution. These require recorded integration evidence.

## Week 9: the server

### Current checkpoint and sequence

Gabriel accepted Nadia's Week 9 proposal in PR #10 on 2026-11-22.
The shared scaffold already reached `main` through PR #9 before this approval;
this record does not retroactively change that sequence. Five scaffold tests
pass, but no flight tools are registered. The failing `search_flights` test
baseline is on `feat/search-flights`, not on `main`.

Hold `feat/search-flights` and further tool implementation until PR #10 is
merged. Patrick's fixture ownership is confirmed by his PR #10 comment, as clarified by Gabriel.
For each tool, preserve separate failing-test and passing-implementation commits.
The proof below specifies required checks, not completed verification; record
actual results in `CHECKS.md` as they are obtained.

### Files to create or change
| File | Purpose |
|---|---|
| `server.py`, `src/fastflights_mcp/server.py`, `src/fastflights_mcp/tools/` | Existing root launcher delegates to the shared package `main()`. Keep one SDK v2 `MCPServer` instance in the package; register tool modules against it. Provider credentials come from the server environment and never appear in results. Use logging; keep server stdout for the protocol. |
| `client.py` | Host with no model. Lists schemas and calls each tool through a real stdio subprocess for the lab transport check. In-memory `Client(mcp)` belongs in offline tests. Record the temporary stray-print experiment, then remove the print. |
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
2. Implement the tool in `src/fastflights_mcp/tools/` and register it on the
   shared application so the tests pass. Schema from type hints
   (`Annotated` + `Field`), `ToolError` for failures the model should see.
   Rewrite the docstring until it says **when** to call the tool. Commit:
   `Server: <name>`.
3. Update `client.py` to call the new tool once; paste the printed schemas
   into `CHECKS.md`.
4. With the server registered in `.mcp.json` and the venv active, start `orclaude`, approve
   the server, run `/mcp`, ask a question that needs the tool, and export
   the transcript under `transcripts/`.

The shared `.mcp.json` already exists. Keep its root-launcher registration.
Use separate tool modules, pull before editing shared registration, and commit
small changes to avoid merge conflicts.

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
- **Tool descriptions guide model selection** together with names and schemas.
  A description must explain when to call the tool. The owner
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
  valid for 30 days. Implement and test cache hits, expiry, and uncached fallback during Week 9.
- **Wrong-type inputs must not run the function body**: a fast error before
  any upstream call is also what protects the SearchApi quota from misuse.

### Proof
For each Week 9 tool, its no-key tests must cover the five misuse cases from lab step 3:

**`search_airports`** required tests:
1. Tool is listed; schema matches spec (`query`: string, 1–100 chars after trim).
2. Wrong type → `is_error` result, function body did not run, no upstream call.
3. Whitespace-only or out-of-range `query` → `is_error` result.
4. No match upstream → normal success with `matches: []` and an explanatory message.
5. Upstream failure (HTTP error, missing key, quota) faked → `ToolError` with an
   actionable message; the key is not in the message.

**`search_flights`** required tests:
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
   and 3 for each tool must verify this: `is_error` true and the fake
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
   Proof item 5 for each tool must assert the key is absent from the `ToolError`
   message. Additional leak paths to check include: a debug
   `print(os.environ)` to stdout (also breaks the JSON-RPC stream), an
   authenticated URL echoed in an error message, or a logged request URL.
   Proof item 5 requires message-leak checks; separately test log sanitization
   and perform the stray-print experiment with `client.py` (lab step 5).
   A protocol-error experiment alone does not prove logs contain no secrets.

**What changed in the plan:**
- Risks: replaced the "Redis unavailability (open per intent)" bullet with
  the decided policy from PR #7 — server logs a warning and serves without
  the cache; tests fake the cache and need neither Redis nor an API key;
  cached lookups stay valid for 30 days. No longer listed as open.
- Proof: requires wrong-type rejection and key-leakage checks; actual passing
  evidence is still pending.
- Answer 2 corrected by hand: the agent first suggested putting the key in the env block of .mcp.json, but that file is committed. The key comes from the launching shell; .mcp.json may only reference ${SEARCHAPI_API_KEY}.


## Completion and approval

Completion requires every deliverable above to have recorded verification,
all required tool behavior to work, the two hosts to complete the shared task,
and both public hosting and package publishing to be usable. Passing only the
initial two tools does not meet this milestone.

Record interim course checkpoints separately: the Week 9 checks/tag do not
claim completion of this full milestone. Finish the final course submission
artifacts and tag only after their actual checks pass.

**Approved by:** Gabriel (Gabbykoms), Nadia Boudhaim Maguire, and Patrick (pmansy1), 2026-11-22. Nadia accepts cache integration and model evaluation. Patrick accepts calendar and price-insight provider-fixture ownership through his [PR #10 comment](https://github.com/Gabbykoms/FastFlightsMCP/pull/10#issuecomment-7088709917), with that meaning confirmed by Gabriel.
