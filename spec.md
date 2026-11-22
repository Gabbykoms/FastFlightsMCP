# FastFlightsMCP: Specification

**Status:** Shell (to be written by the team)
**Date:**
**Inputs:** `intent/*.md`, `README.md`, Team Project 2 sheet
**No code is written in this document.** Interfaces are signatures and payload shapes only.

## 1. The task

<!-- The multi-step task the agent carries out through the server, in one paragraph. -->

## 2. Global decisions

### Language
<!-- Decision, and the alternative considered. -->

### Model
<!-- A cheap model and a stronger one, both with tool calling; the alternative considered. -->

## 3. The server's tools

<!-- One subsection per tool: name, when the model should call it, arguments with
     types and limits, result shape, "not found" result, error results. -->

### 3.1 `<tool name>`

| Argument | Type | Required | Limits / notes |
|---|---|---|---|
| | | | |

**Result:**

**Not found (normal result):**

**Errors (error results):**

## 4. Components

<!-- For each component (server, own loop, ...): what it does, language decision with
     alternative, model decision with alternative, interfaces, failure handling. -->

## 5. Side effects and the gate

<!-- Every tool that writes, sends, deletes, or spends: what it records as pending,
     who confirms it, or exactly why it is safe to run unattended. -->

## 6. Limits

<!-- The loop's step cap and cost cap per task. -->

## 7. Dependencies

| Dependency | Reason |
|---|---|
| `mcp` | |
| `pytest` | |

## 8. Cost estimate
