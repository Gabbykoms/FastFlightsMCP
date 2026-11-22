# FastFlightsMCP: Our own agent loop

Status: Draft, agreed by team 2026-11-22.
Named team approver: Nadia Boudhaim Maguire

## Purpose

The agent loop is a second host that runs the same multi-step task as Claude Code against our MCP server. Given a round trip described in plain words plus a date window, the loop resolves airports, finds cheapest dates, searches flights, and recommends one with reasons. It uses the openrouter SDK. Step and cost caps keep a runaway loop affordable.

## User experience

The host runs `agent.py` with the user's prompt. The loop calls `search_airports`, then `find_cheapest_dates`, then `search_flights`, then writes a recommendation paragraph. After every step it prints a one-line console log: tool name, args, short result, tokens used, cost. At the end the final recommendation text is printed and one full run is saved under `transcripts/`.

If a tool returns an error, the error text is passed back to the model unchanged on the next turn. The model decides what to do; the step and cost caps stop the loop if it keeps going.

## Success criteria

- Step cap: 8, a constant in code. Cost cap: $0.20 USD per run, summed from OpenRouter's reported `usage` (prompt + completion tokens × model price). Either cap stops the loop.
- One openrouter model per run. The eval compares `minimax/minimax-m3` against `anthropic/claude-haiku-4.5`; the final choice is recorded in `spec.md`.
- Task: given a round trip in plain words plus a date window, resolve airports, find cheapest dates, search flights, recommend one with reasons.
- openrouter SDK, no streaming. Tool error text is passed back to the model unchanged. Error text never contains the API key.
- Final output: a final recommendation plus a console step log with `tool`, `args`, a short `result`, `tokens`, and `cost` per step. One run saved under `transcripts/`.

## Open decisions

- Which model wins the eval — the final choice between `minimax/minimax-m3` and `anthropic/claude-haiku-4.5` is recorded in `spec.md`.
- Whether to also enforce a wall-clock timeout on top of the step and cost caps.
- The saved run under `transcripts/` is committed, as the brief requires.
