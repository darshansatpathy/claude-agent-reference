# ADR 0004: Evaluate the harness with scripted transcripts, and the model separately

## Status

Accepted

## Context

Most of what makes an agent safe to run is not the model: it is whether approval is enforced,
limits stop the run, errors are reported, retries are bounded and secrets stay out of logs.
Those properties should be tested on every commit, deterministically and for free. Model
quality needs a different instrument.

## Decision

Replay recorded API responses through a scripted transport and assert on what the loop does.
Every scenario is also checked against the API's own request rules. The model's quality is
**not** measured here. A separate manual smoke test calls the live API for three checks, one
of which asserts a safety property (an unapproved write changes nothing) that holds whatever
the model decides.

A scenario must fail when its mechanism is broken. Before release each mechanism was disabled
in turn and the suite was required to catch it.

## Consequences

- The suite is fast, offline and stable, and it runs on three Python versions in CI.
- A green suite says the harness is correct. It does not say a model is good at your task,
  and the README says so.
- Real incidents can be turned into regression scenarios by pasting the recorded responses.
