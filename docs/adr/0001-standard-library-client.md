# ADR 0001: A standard-library HTTP client instead of the official SDK

## Status

Accepted

## Context

Anthropic publishes an official SDK that already retries, parses responses and supports
streaming. For production code that is usually the right choice. This repository has a
different purpose: to show, in code a reviewer can read in minutes, what a production client
has to get right (retry classification, `Retry-After`, credential handling, error shapes), and
to run its safety evals with no network and no installed dependency.

## Decision

Implement the client on `urllib` behind a two-method `Transport` interface. The package has no
runtime dependencies. TLS verification is always on, https is required (loopback http is
allowed for local test servers), and the API key is sent only to the configured `base_url`.

## Consequences

- Everything the agent does is visible and testable. Tests and evals swap the transport and
  never touch the network; a loopback test exercises the real `urllib` path.
- Supply-chain exposure is minimal: nothing to audit but the standard library.
- There is no streaming and no async. A service that needs them should use the SDK. The
  agent, tool, budget and audit layers sit on a `create(request) -> Message` call, so moving
  them to the SDK is a small change.
- Wire details (batch endpoints, error shapes) are maintained here and need re-checking
  against the API documentation when it changes.
