# FastFlightsMCP

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants (Claude Desktop, Claude Code, Cursor, etc.) search **Google Flights** for real flight options, prices, and dates through plain conversation.

> "Find me the cheapest round trip from NYC to Lisbon sometime in March, under $600, nonstop if possible."

The assistant calls FastFlightsMCP's tools, gets structured flight data back, and reasons over it: comparing options, flexing dates, and explaining trade-offs.

> **Status:** Early design / pre-alpha. This README describes the intended shape of the project. Nothing below is implemented yet.

---

## Why

Google Flights has the best fare coverage of any consumer search engine, but it has **no official public API** (Google retired QPX Express in 2018). FastFlightsMCP fills that gap for AI assistants by:

- Building Google Flights search queries programmatically
- Parsing the results into clean, typed JSON
- Exposing them as MCP tools that an LLM can call

## How it works

```
┌──────────────┐   MCP (stdio / HTTP)   ┌──────────────────┐   HTTPS   ┌────────────────┐
│ MCP client   │ ─────────────────────▶ │  FastFlightsMCP  │ ────────▶ │ Google Flights │
│ (Claude etc.)│ ◀───────────────────── │  server (Python) │ ◀──────── │                │
└──────────────┘    structured JSON     └──────────────────┘   HTML    └────────────────┘
```

1. The client calls a tool such as `search_flights` with origin, destination, dates, and filters.
2. The server encodes the search into a Google Flights query (the protobuf-encoded `tfs` URL parameter Google Flights uses).
3. It fetches the results page and parses itineraries: airlines, times, stops, duration, price.
4. Results are normalized, cached briefly, and returned to the client with a Google Flights link the user can open to book.

### Planned stack

| Concern         | Choice                                                                 |
| --------------- | ---------------------------------------------------------------------- |
| Language        | Python 3.11+                                                           |
| MCP framework   | [`mcp`](https://github.com/modelcontextprotocol/python-sdk) (FastMCP)  |
| Flights backend | [`fast-flights`](https://github.com/AWeirdDev/fast-flights) (Google Flights query builder + parser) |
| Validation      | Pydantic models for tool inputs and outputs                            |
| Packaging       | `uv` / `pyproject.toml`, runnable through `uvx fastflights-mcp`        |

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

```bash
claude mcp add fastflights -- uvx fastflights-mcp
```

---

## Configuration

| Env var                   | Default  | Description                                       |
| ------------------------- | -------- | ------------------------------------------------- |
| `FASTFLIGHTS_CURRENCY`    | `USD`    | Default currency for prices                       |
| `FASTFLIGHTS_LANGUAGE`    | `en`     | Language for Google Flights results               |
| `FASTFLIGHTS_CACHE_TTL`   | `600`    | Seconds to cache identical searches               |
| `FASTFLIGHTS_RATE_LIMIT`  | `10`     | Max outbound requests per minute                  |
| `FASTFLIGHTS_FETCH_MODE`  | `common` | `common` (direct HTTP) or `fallback` (headless browser when blocked) |

---

## Project layout (planned)

```
FastFlightsMCP/
├── src/fastflights_mcp/
│   ├── server.py        # MCP server + tool registration
│   ├── tools/           # One module per tool
│   ├── google/          # Query building, fetching, parsing
│   ├── models.py        # Pydantic input/output schemas
│   ├── airports.py      # IATA lookup (bundled airport dataset)
│   └── cache.py         # TTL cache + rate limiter
├── tests/
│   ├── fixtures/        # Saved Google Flights responses for offline tests
│   └── ...
├── pyproject.toml
└── README.md
```

---

## Roadmap

- [ ] **v0.1** Core server, `search_flights` (one-way + round-trip), `search_airports`
- [ ] **v0.2** Caching, rate limiting, and sturdier parsing with fixture-based tests
- [ ] **v0.3** `find_cheapest_dates` and `search_multi_city`
- [ ] **v0.4** Price insights, streamable HTTP transport for remote hosting
- [ ] **Later** Price-watch alerts, alternate airports, and baggage/fare-class details

---

## Limitations & disclaimer

- **Unofficial.** This project is not affiliated with or endorsed by Google. It reads publicly visible Google Flights results; Google can change its page format at any time, which may break parsing until the project is updated.
- **Respect the source.** Built-in caching and rate limiting keep traffic low. Don't use this for bulk scraping, and review Google's Terms of Service for your use case.
- **Prices aren't guaranteed.** Fares change constantly. Always confirm on the booking link before purchasing.
- **No booking.** FastFlightsMCP searches only; it never purchases tickets or handles payment.

## License

MIT (TBD)
