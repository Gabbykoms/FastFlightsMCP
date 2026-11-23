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

## Pull requests

- When Gabriel asks to make a PR, prepare a detailed title and description
  automatically. Show the proposed text and wait for his approval before
  creating the PR, including a draft PR.
- Lead with the problem, resulting behavior, and scope of the change.
- List new files and explain each file's purpose. Separately list existing
  files edited and explain what changed in each. Use tables when helpful.
- Name the functions implemented or materially changed, and explain their
  responsibilities, inputs, outputs, and relevant behavior. Explain important
  shared objects, entry points, and configuration changes too.
- Describe added or changed tests and what each verifies. Report actual
  validation results, distinguishing mocked checks from live integration checks.
- Include known limitations, unresolved issues, and remaining work. Do not
  imply that deployment, publishing, or unperformed checks have succeeded.
- Include relevant commit IDs when they help reviewers follow the work, such
  as separate failing-test and implementation commits.
- Write for a reviewer who has not read the conversation. Do not substitute a
  short summary for these details unless Gabriel explicitly requests brevity.
