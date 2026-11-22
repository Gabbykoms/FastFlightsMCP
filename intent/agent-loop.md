# FastFlightsMCP: Our own agent loop

Status: Draft, agreed by team 2026-11-22.
Named team approver: Nadia Boudhaim Maguire

## Purpose

The agent loop is a second host that runs the same multi-step task as Claude Code against our MCP server. Given a round trip described in plain words plus a date window, the loop resolves airports, finds cheapest dates, searches flights, and returns up to five matching flights with one best-overall recommendation and its reasons, following the search-flights intent: three within budget and two alternatives up to 20% over budget that the user may still consider. It uses the openrouter SDK. Step and cost caps keep a runaway loop affordable.

## User experience

The host runs `agent.py` with the user's prompt. The loop calls `search_airports`, then `find_cheapest_dates`, then `search_flights`, then writes the result: up to five flights, with the within-budget and over-budget groups clearly separated, and one recommendation explained. After every step it prints a one-line console log: tool name, args, short result, tokens used, cost. At the end the final recommendation text is printed and one full run is saved under `transcripts/`.

If a tool returns an error, the error text is passed back to the model unchanged on the next turn. The model decides what to do; the step and cost caps stop the loop if it keeps going.

## Success criteria

- Step cap: 8, a constant in code. Cost cap: $0.05 USD per run, summed from OpenRouter's reported `usage` (prompt + completion tokens × model price). Either cap stops the loop.
- One openrouter model per run. The eval compares `minimax/minimax-m3` against `anthropic/claude-haiku-4.5`; the final choice is recorded in `spec.md`.
- Task: given a round trip in plain words plus a date window, resolve airports, find cheapest dates, search flights, and return up to five matching flights (three within budget, two up to 20% over) with one best-overall recommendation and its reasons.
- No wall-clock timeout on the whole run: a fixed limit could cut short legitimately longer searches, such as multi-city trips. The step and cost caps bound every run.
- openrouter SDK, no streaming. Tool error text is passed back to the model unchanged. Error text never contains the API key.
- Final output: the matching flights and the explained recommendation, plus a console step log with `tool`, `args`, a short `result`, `tokens`, and `cost` per step. One run saved under `transcripts/`.
- The loop is non-interactive: every task prompt states origin, destination, dates, and cabin, because the search-flights intent requires the host to ask for these when missing.

## Open decisions

- Which model wins the eval — the final choice between `minimax/minimax-m3` and `anthropic/claude-haiku-4.5` is recorded in `spec.md`.
- Whether $0.05 per run is enough for the stronger eval model, or whether the cap differs per model. Record actual per-run costs in the eval before changing it.
- The saved run under `transcripts/` is committed, as the brief requires.
