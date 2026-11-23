# FastFlightsMCP development

- Python 3.11+. The MCP server is `server.py` at the repository root; `client.py` is a host with no model; tests live in `tests/`.
- Setup: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
- Verify: `python3 -m pytest tests -v` (no key, no network, no model).
- Every dependency in `requirements.txt` is listed under Dependencies in `spec.md` with its reason.
- Follow `spec.md` for tool names and schemas, and `plan.md` for the order of work.

## Working rules

- Gabriel wants to understand and own important project decisions. Explain
  tradeoffs and get his approval before introducing new technologies or making
  major architecture, cost, security, or deployment decisions. Proceed with
  routine implementation within already agreed decisions.
- Week 9: the MCP server is server.py, built with the mcp SDK version 2
  (MCPServer from mcp.server; not FastMCP). Stages assigned: plan, tests.
- Tests for a tool are written before the tool's code, and go through the
  protocol with an in-memory Client, with no model and no network.
- Nothing is printed to stdout: the host reads it as protocol. Use logging.
- Credentials are read from the server's environment and never returned in a
  result. A tool with a side effect records a pending request; it never
  completes the effect on a model's request.
- Tests never call Google Flights: fake the upstream response or failure.
