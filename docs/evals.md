# Evaluations

## What these evaluate

The 27 scenarios in `evals/scenarios/` test the **harness**, not the model. A scripted
transport replays a recorded sequence of API responses, so each scenario is deterministic,
free, and runs without a key or a network.

That makes them good at the things that must never depend on the model's mood:

- a write tool without approval does nothing, even if the model asks for it politely or has
  just read a document telling it to;
- every limit stops a run that should be stopped;
- a tool that fails, a call that is invalid and a tool that does not exist are all reported,
  and the model can recover;
- retries are bounded and a credential error is never retried;
- credentials and personal data do not reach the audit log;
- cache reads are counted and priced differently.

```bash
claude-agent eval                       # all scenarios
claude-agent eval --only budget         # a subset
claude-agent eval --format markdown     # for a CI summary
claude-agent demo injection-in-document-cannot-refund
```

## API contract checks on every scenario

Whatever a scenario asserts, the runner also checks each request the agent sent against rules
the real API enforces: the conversation starts with a user message and alternates, every
`tool_use` gets a matching `tool_result`, `tool_result` blocks come first in their message,
there are at most four cache breakpoints, and the API key never appears in a request body.

## Do the scenarios fail when they should?

A test that cannot fail proves nothing. Before release, each safety mechanism was disabled in
turn (approval, each budget, the retry classifier, the redactor, the max-tokens guard, the
untrusted wrapper, result ordering, the cache-read price) and the suite had to report a
failure each time. It did, for all sixteen. When you add a scenario, do the same.

## What they do not tell you

Whether a model chooses the right tool, writes a good answer or resists a given injection is
measured against the model, with your data. Two things in this repository help:

- `claude-agent smoke` runs three live checks against the real API: a plain request, a tool
  round trip, and an unapproved write that must change nothing. The last is a safety property
  that holds whatever the model decides. It is a manual workflow, `live-smoke.yml`.
- The scenario format is the model for recording real transcripts: copy a captured response
  sequence into a scenario to turn an incident into a regression test.

For model quality, build a labelled dataset from your own tasks and score it. The companion
project [agent-eval-harness](https://github.com/darshansatpathy/agent-eval-harness) is a place
to start.
