# Operations

## Configuration

| Setting | Where | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | environment | required |
| `ANTHROPIC_BASE_URL` | environment | `https://api.anthropic.com` (https required; plain http only on loopback) |
| `CLAUDE_AGENT_MODEL` | environment, used by `smoke` | `claude-sonnet-5-5` |
| `ClientConfig.timeout_s` | code | 120 s per HTTP call |
| `ClientConfig.max_retries` | code | 3 |
| `ClientConfig.max_retry_after_s` | code | 60 s. A longer `Retry-After` is raised, not slept on. |
| `Budget` | code | 12 turns, 30 tool calls, 200,000 tokens, 300 s, 3 identical calls |

The default model is a convenience. Pin the model id you have tested and evaluated, and
change it deliberately.

## Retry policy

| Response | Behaviour |
|---|---|
| network error, timeout | retry |
| 408, 409, 500, 502, 503, 504, 529 | retry |
| 429 | retry, waiting at least `Retry-After`; if that is longer than `max_retry_after_s`, raise |
| 400, 401, 403, 404, 413 and anything else | raise immediately |

Waits use exponential backoff capped at `backoff_cap_s`, with full jitter. A spend-limit 429
carries no `Retry-After` and will keep failing, which is why retries are bounded.

## What to monitor

Every model call and tool call writes an audit event. These are the fields worth alerting on:

- `run_end.terminated_by` other than `end_turn`: a limit or fault stopped the run. Rising
  `budget:*` values mean prompts, tools or limits need attention.
- `tool_call.outcome` of `denied`: the model is attempting writes. Expected sometimes; a spike
  suggests injection or a prompt problem.
- `tool_call.outcome` of `invalid` or `error`: schema or tool bugs.
- `model_call.usage.cache_read_input_tokens` near zero after the first turn: caching is
  broken ([cost and caching](cost-and-caching.md)).
- `model_call.request_id`: quote it to Anthropic support. It is also in `RunResult.request_ids`.

Verify an audit file at any time with `claude-agent verify-audit audit.jsonl`.

## Long requests and streaming

This client requests complete responses. Long generations can hit the HTTP timeout or the
API's guidance to stream; for those, use streaming in your service and keep the same
budget, tool and audit layers. Retrying a streamed response after partial output needs care:
decide whether to discard or resume the partial text.

## Batches

Use `BatchClient` for evaluations, backfills and classification where nobody is waiting.
Create, `wait`, then `results`; match on `custom_id`, because results are not returned in
request order. Handle all four result types (`succeeded`, `errored`, `canceled`, `expired`).
