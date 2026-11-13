# Threat model

An agent combines a model that follows instructions, tools that act, and text from places you
do not control. The goal here is that **a compromised or confused model can do only what the
tools, the approver and the budget allow**.

## Trust boundaries

1. Your code to the Claude API (network, credentials).
2. The model's output to your tools (the model is not trusted to choose safely).
3. External content into the model's context (documents, web pages, tool results).
4. The audit log to its readers.

## Threats and controls

| # | Threat | Control | Where it is tested |
|---|---|---|---|
| T1 | Prompt injection in a document makes the model call a write tool | Writes need approval regardless of what the model has read. Untrusted tool output is wrapped and labelled. | `injection-in-document-cannot-refund` |
| T2 | The model invents a tool or passes malformed arguments | Unknown tools refused; inputs validated against the schema before any handler runs | `unknown-tool`, `invalid-input-recovers` |
| T3 | Runaway loop burns money | Limits on turns, tool calls, tokens, cost, time; identical calls blocked | `budget-*`, `identical-call-loop` |
| T4 | Output is cut off mid tool call and a partial call runs | `max_tokens` ends the run; the call is not executed | `max-tokens-mid-tool-call` |
| T5 | API key leaks through logs, errors or requests | Key excluded from `repr`, never in request bodies, redacted from logs, sent only to `base_url` | `test_client.py`, contract checks |
| T6 | Credentials sent to the wrong host (config or redirect abuse) | Same-origin check; https required except loopback | `test_credentials_are_never_sent_to_another_origin` |
| T7 | Sensitive data lands in the audit log | Fingerprints by default; content logging is opt-in and redacted | `audit-redaction` |
| T8 | An audit record is altered or removed after the fact | Hash chain detects edits, deletions and reordering | `test_audit.py` |
| T9 | A retry storm makes an outage worse | Bounded retries, exponential backoff with full jitter, `Retry-After` honoured, long waits surfaced | `retry-*` scenarios |
| T10 | A tool bug leaks internals to the model | Failures return the exception type only, never the message or a stack trace | `tool-failure-reported` |
| T11 | Oversized tool output floods the context | Per-tool output cap with an explicit truncation marker | `test_tools.py` |
| T12 | Cost figures are wrong and a budget gives false comfort | Prices are configuration; a cost budget with no price for the model refuses to start | `test_agent.py` |

## What this does not solve

- **The wrapper is a mitigation.** Delimiting untrusted text helps a model treat it as data, but
  models can still be talked into things. That is why T1 relies on approval, not on the wrapper.
- **The approver is yours.** A rubber-stamp approver defeats the control. Approve on the
  specific arguments, show them to a person, and log who said yes.
- **Truncation of the audit log tail** is not detectable from the file alone. Store the latest
  chain hash somewhere the agent cannot write, and compare.
- **Read tools can still exfiltrate.** A model that can read secrets and also call a network
  tool can leak them. Scope read tools to what the task needs; the companion project
  [agent-guardrails-gateway](https://github.com/darshansatpathy/agent-guardrails-gateway)
  adds task-scoped grants and taint tracking at the tool boundary.
- **Model quality and safety** are properties of the model and your prompts and are measured by
  your own evaluations, not by this harness ([evals](evals.md)).
