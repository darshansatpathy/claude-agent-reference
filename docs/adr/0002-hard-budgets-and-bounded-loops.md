# ADR 0002: Every run has hard limits, and hitting one is a result, not a crash

## Status

Accepted

## Context

An agent loop ends when the model decides to stop. A model that is confused, manipulated or
simply stuck will not decide to. Without limits the cost, the time and the number of actions
are unbounded.

## Decision

Six limits, checked before each model call: turns, tool calls, total tokens, estimated cost,
wall-clock time and repeats of an identical tool call. Reaching one ends the run with
`terminated_by = "budget:<limit>"` and a normal `RunResult`, so the caller can show a partial
answer, retry with a larger limit or escalate. `RunResult.completed` is true only when the
model finished on its own. Even when the tool-call limit is reached mid-turn, every `tool_use`
still receives a result, because the API requires one for each.

A cost limit with no price for the model refuses to start. A budget that silently measures
nothing is worse than none.

## Consequences

- The worst case for a run is known in advance: at most `max_turns` calls of at most
  `max_tokens` output each.
- Limits are checked before a call, so the last call can overshoot token and cost limits by
  its own size. The docs say so.
- Callers must handle non-`end_turn` outcomes. That is the point.
