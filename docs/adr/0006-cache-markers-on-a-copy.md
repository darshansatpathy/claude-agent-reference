# ADR 0006: Add cache breakpoints to a copy of each request

## Status

Accepted

## Context

Prompt caching needs `cache_control` markers, and the API accepts only a few per request. An
agent that appends a marker to the conversation each turn would exceed the limit after a few
turns, and a marker left on an old block is no longer where the cache should end.

## Decision

Keep the stored conversation free of markers. When building each request, deep-copy it and
mark the end of the system prompt, the last tool definition and the last block of the
conversation. The last marker therefore moves forward every turn. Tools are sent in sorted
order so the prefix is stable.

## Consequences

- At most three breakpoints per request, enforced by a contract check on every scenario.
- A deep copy per turn costs little next to a network call.
- Caching never changes behaviour; it changes price and latency. If the prefix is too short or
  keeps changing, nothing is cached and the usage counters show it.
