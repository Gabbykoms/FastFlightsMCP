# FastFlightsMCP

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants (Claude Desktop, Claude Code, Cursor, etc.) search **Google Flights** for real flight options, prices, and dates through plain conversation.

> "Find me the cheapest round trip from NYC to Lisbon sometime in March, under $600, nonstop if possible."

The assistant calls FastFlightsMCP's tools, gets structured flight data back, and reasons over it: comparing options, flexing dates, and explaining trade-offs.

> **Status:** Early design / pre-alpha. This README describes the intended shape of the project. Nothing below is implemented yet.

---

## Why

Google Flights has the best fare coverage of any consumer search engine, but it has **no official public API** (Google retired QPX Express in 2018). FastFlightsMCP fills that gap for AI assistants by:

- Querying SearchApi for Google Flights data
- Normalizing provider responses into clean, typed JSON
- Exposing them as MCP tools that an LLM can call

## How it works

```
┌──────────────┐   MCP (stdio / HTTP)   ┌──────────────────┐   HTTPS   ┌────────────────┐
│ MCP client   │ ─────────────────────▶ │  FastFlightsMCP  │ ────────▶ │ SearchApi      │
│ (Claude etc.)│ ◀───────────────────── │  server (Python) │ ◀──────── │                │
└──────────────┘    structured JSON     └──────────────────┘   JSON    └────────────────┘
```

1. The client calls a tool such as `search_flights` with origin, destination, dates, and filters.
2. The server validates the request and calls the appropriate SearchApi engine using server-held credentials.
3. It completes any required flight selections and normalizes itineraries: airlines, times, stops, duration, and price.
4. Eligible results are returned with Google Flights links. Week 9 runs without caching; final-release caching is planned, with the backend still under discussion.

### Planned stack

| Concern         | Choice                                                                 |
| --------------- | ---------------------------------------------------------------------- |
| Language        | Python 3.11+                                                           |
| MCP framework   | Official [`mcp`](https://github.com/modelcontextprotocol/python-sdk) SDK v2: `MCPServer` from `mcp.server`, as required by the lab |
| Flights backend | [SearchApi](https://www.searchapi.io/docs/google-flights-api) for all five tools, accessed through `httpx` |
| Validation      | Pydantic models for tool inputs and outputs                            |
| Packaging       | setuptools build backend in `pyproject.toml`; published command runnable through `uvx fastflights-mcp` |

The SDK version and installation requirements will be checked against the professor's worked example before implementation. Both the root `server.py` launcher and the published `fastflights-mcp` console command will call the same `main()` entry point in `fastflights_mcp.server`. Publishing through `uvx` remains part of the project.

### Week 9 working rules

Follow the [lab instructions](lab.md), with `search_flights` and `search_airports` as the initial tools. The other tools and HTTP hosting remain on the roadmap.

- Record tool contracts and dependency reasons in `spec.md`, including `mcp` and `pytest`. Maintain `requirements.txt` alongside `pyproject.toml` for the lab setup.
- Write the Week 9 plan before code: responsibilities, work order, risks, and proof for each tool. Answer at least two lab review questions and record **Approved by** in `plan.md`.
- Record the lab's working rules in `CLAUDE.md`; the assigned AI-assisted stages are plan and tests. Write and run tests before implementing each tool, and commit the failing tests separately.
- Test through the MCP protocol with an in-memory `Client(mcp)`, with no model, network, or subprocess. Each tool needs at least five tests: discovery/schema, wrong types without executing the function, out-of-range inputs, normal empty/not-found results, and simulated backend failures.
- Define schemas with type hints and `Annotated`/`Field` descriptions and limits. Specify input bounds and date rules in `spec.md`. Docstrings must explain when to call each tool; team members review and rewrite them.
- Return actionable failures through `ToolError`. Use logging; never print to the server's stdout, which carries protocol messages.
- Read credentials from the server environment and never return them in tool results. Ignore `.venv/`, secrets, and pending/outbox files in Git.
- The initial tools are read-only. Any future tool with a side effect records a pending request for a person to complete. Its tests must prove that the request is recorded and nothing has left the machine.
- Record actual verification evidence in `CHECKS.md`: client schemas/results, the temporary stdout-breakage experiment (remove the print afterward), test counts, Claude Code behavior, server-versus-script reasoning, and the side-effect gate or first planned side-effect tool.

---

## Tools

### `search_flights`
Search one-way or round-trip flights for specific dates.

| Param            | Type                                                  | Required | Notes                              |
| ---------------- | ----------------------------------------------------- | -------- | ---------------------------------- |
| `origin`         | string                                                | yes      | IATA code, e.g. `JFK`              |
| `destination`    | string                                                | yes      | IATA code, e.g. `LIS`              |
| `depart_date`    | date (`YYYY-MM-DD`)                                   | yes      |                                    |
| `return_date`    | date                                                  | no       | Omit for one-way                   |
| `adults`         | int                                                   | no       | Default `1`                        |
| `children`       | int                                                   | no       | Default `0`                        |
| `infants`        | int                                                   | no       | Default `0`                        |
| `cabin`          | `economy` \| `premium_economy` \| `business` \| `first` | no       | Default `economy`                  |
| `max_stops`      | int                                                   | no       | `0` = nonstop only                 |
| `max_price`      | int                                                   | no       | In `currency`                      |
| `currency`       | string                                                | no       | Default `USD`                      |
| `sort_by`        | `best` \| `price` \| `duration` \| `departure`        | no       | Default `best`                     |
| `limit`          | int                                                   | no       | Default `10`                       |

### `search_multi_city`
Search an itinerary with 2 to 6 legs (e.g. `JFK→LHR`, `LHR→CDG`, `CDG→JFK`).

### `find_cheapest_dates`
Given a route and a date window (e.g. "any 7-night trip in March"), scan dates and return the cheapest departure/return combinations. Useful for flexible travelers.

### `search_airports`
Resolve a city or airport name to IATA codes (`"new york"` → `JFK`, `LGA`, `EWR`) so the model doesn't have to guess.

### `get_price_insight` *(stretch)*
Return Google's "prices are currently low / typical / high" signal for a route when available.

---

## Example response

```json
{
  "query": {
    "origin": "JFK",
    "destination": "LIS",
    "depart_date": "2027-03-10",
    "return_date": "2027-03-17",
    "adults": 1,
    "cabin": "economy"
  },
  "price_level": "low",
  "results": [
    {
      "price": 512,
      "currency": "USD",
      "airlines": ["TAP Air Portugal"],
      "stops": 0,
      "duration_minutes": 405,
      "departure": "2027-03-10T19:30",
      "arrival": "2027-03-11T07:15",
      "is_best": true
    }
  ],
  "google_flights_url": "https://www.google.com/travel/flights?tfs=...",
  "fetched_at": "2027-01-04T15:02:11Z"
}
```

---

## Installation (planned)

These commands describe the intended setup once the implementation and packaging files exist. The `uvx` command requires publishing the package first.

```bash
# Run directly
uvx fastflights-mcp

# Or install from source
git clone https://github.com/gabbykoms/FastFlightsMCP.git
cd FastFlightsMCP
uv sync
uv run fastflights-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fastflights": {
      "command": "uvx",
      "args": ["fastflights-mcp"]
    }
  }
}
```

### Claude Code

For the Week 9 lab, commit this `.mcp.json` in the repository root:

```json
{
  "mcpServers": {
    "fastflights": {
      "command": "python3",
      "args": ["server.py"]
    }
  }
}
```

With the planned files in place, set up the local package so the root launcher can import it:

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
python3 client.py
orclaude
```

Start `orclaude` from the repository root with the virtual environment active, approve the server, and check `/mcp`. Ask a question requiring a tool, then export the session with `/export transcripts/week09-claude-code.txt`. Every teammate runs the verification locally; one committed transcript is sufficient. If a side-effect tool is added, exercise its pending-request behavior too.

Published-package installation remains available as a separate planned option:

```bash
claude mcp add fastflights -- uvx fastflights-mcp
```

---

## Configuration

| Env var                   | Default  | Description                                       |
| ------------------------- | -------- | ------------------------------------------------- |
| `FASTFLIGHTS_CURRENCY`    | `USD`    | Default currency for prices                       |
| `FASTFLIGHTS_LANGUAGE`    | `en`     | Language for Google Flights results               |
| `FASTFLIGHTS_CACHE_TTL` | TBD | Post-Week-9 setting; cache backend and TTL remain under review |
| `FASTFLIGHTS_RATE_LIMIT`  | `10`     | Max outbound requests per minute                  |
| `SEARCHAPI_API_KEY` | Required | Server-only SearchApi credential; never commit or return it |

---

## Project layout (planned)

```
FastFlightsMCP/
├── src/fastflights_mcp/
│   ├── server.py        # MCPServer, tool registration, shared main() entry point
│   ├── tools/           # One module per tool
│   ├── google/          # SearchApi requests and response normalization
│   ├── models.py        # Pydantic input/output schemas
│   ├── airports.py      # IATA lookup (bundled airport dataset)
│   └── cache.py         # TTL cache + rate limiter
├── tests/
│   ├── fixtures/        # Saved Google Flights responses for offline tests
│   ├── test_search_flights.py  # In-memory MCP protocol tests
│   └── test_search_airports.py
├── server.py            # Root launcher calling the package's main()
├── client.py            # Host without a model; lists schemas and calls tools
├── .mcp.json            # Shared local Claude Code registration
├── .gitignore
├── pyproject.toml       # uv packaging + fastflights-mcp console entry point
├── requirements.txt     # Lab dependencies, including mcp and pytest
├── spec.md              # Tool contracts and dependency reasons
├── plan.md              # Week 9 plan, responsibilities, proof, approval
├── CLAUDE.md            # Working rules
├── CHECKS.md            # Actual verification output and findings
├── transcripts/
│   └── week09-claude-code.txt
├── lab.md               # Downloaded professor's instructions
└── README.md
```

---

## Roadmap

Week 9 follows the lab's commit sequence: working rules, approved plan, failing tests per tool, implementation per tool, then checks and findings. After verification, tag the submission `week09-submitted` and push the commits and tag. The team makes one Moodle submission.

- [ ] **v0.1** Core server, `search_flights` (one-way + round-trip), `search_airports`
- [ ] **v0.2** Caching, rate limiting, and provider-response handling with fixture-based tests
- [ ] **v0.3** `find_cheapest_dates` and `search_multi_city`
- [ ] **v0.4** Price insights, streamable HTTP transport for remote hosting
- [ ] **Later** Price-watch alerts, alternate airports, and baggage/fare-class details

---

## Limitations & disclaimer

- **Unofficial.** This project is not affiliated with or endorsed by Google. It uses SearchApi to retrieve Google Flights data; upstream response changes can require adapter updates.
- **Respect the source.** Built-in caching and rate limiting keep traffic low. Don't use this for bulk scraping, and review Google's Terms of Service for your use case.
- **Prices aren't guaranteed.** Fares change constantly. Always confirm on the booking link before purchasing.
- **No booking.** FastFlightsMCP searches only; it never purchases tickets or handles payment.

## License

MIT (TBD)

## Cost ledger

Record actual billed usage after each evaluation or live verification. Model
charges and SearchApi charges are separate; a model-free client can still incur
SearchApi charges. No spending totals have been verified for this draft.

| Category | Recorded total | Evidence |
|---|---|---|
| OpenRouter model calls | Pending reconciliation | Provider usage and evaluation logs |
| SearchApi searches | Pending reconciliation | Attempt counts and provider billing records |
| Hosting / distribution | Pending setup | Deployment and release records |

Explain project totals above about $10, as requested by the course brief.
