# Claude agent reference

[![CI](https://github.com/darshansatpathy/claude-agent-reference/actions/workflows/ci.yml/badge.svg)](https://github.com/darshansatpathy/claude-agent-reference/actions/workflows/ci.yml)
[![CodeQL](https://github.com/darshansatpathy/claude-agent-reference/actions/workflows/codeql.yml/badge.svg)](https://github.com/darshansatpathy/claude-agent-reference/actions/workflows/codeql.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![Typed](https://img.shields.io/badge/mypy-strict-informational)
![Dependencies](https://img.shields.io/badge/runtime%20dependencies-0-success)
![License](https://img.shields.io/badge/license-MIT-green)

**What a production agent on the Claude API has to get right, in about 2,000 lines you can read in an
afternoon.** No runtime dependencies, strict types, and a test suite that runs offline.

An agent is a model, some tools, and text from places you do not control. The model will
sometimes be wrong and sometimes be fooled. This reference makes the parts around it behave
anyway: writes need a person, every run has hard limits, failures are reported not hidden, and
there is a tamper-evident record of what happened.

```text
prompt ─▶ Agent ─▶ MessagesClient ─▶ Claude API
            │  ▲         retries, backoff, Retry-After
            │  └─ tool results (validated, bounded, labelled)
            ▼
        ToolRegistry ── reads ─────────────▶ run
                      ── writes ── approver ─▶ run only if a person says yes
                      ── untrusted output ──▶ wrapped as data
   Budget (turns · tool calls · tokens · cost · time · repeats)    AuditLog (redacted, hash-chained)
```

## What it covers

| Concern | What the code does |
|---|---|
| **Reliability** | Bounded retries on 408, 409, 429, 5xx and 529 with full-jitter backoff; honours `Retry-After` but will not sleep past a cap; credential and request errors are never retried. |
| **Tool use** | Schema-validates model-produced input, answers every `tool_use` id in one message with results first, reports failures to the model without leaking internals, caps output size. |
| **Safety** | Write tools are denied unless an approver says yes. Text from outside is wrapped as data. Truncated tool calls are never run. |
| **Cost control** | Limits on turns, tool calls, tokens, estimated cost, wall-clock time and identical repeats. A cost limit without a price refuses to start. |
| **Prompt caching** | Breakpoints on system, tools and conversation, applied to a copy of each request; cache reads priced separately. |
| **Batches** | Create, wait, fetch and cost Message Batches, with all four result types handled. |
| **Audit** | One JSON line per event, hash-chained, redacted, fingerprints by default. `verify-audit` detects edits and deletions. |
| **Evaluation** | 27 scripted harness scenarios, API-contract checks on every request, an offline demo, and an opt-in live smoke test. |

## See it work

No API key or network needed:

```bash
pip install -e ".[dev]"
claude-agent demo                       # a refund is requested; nobody approved it
claude-agent eval                       # 27 scenarios
```

```text
User: Refund 20 USD on A-1002, it arrived damaged.
Agent calls lookup_order({'order_id': 'A-1002'})
Agent calls issue_refund({'order_id': 'A-1002', 'amount': 20, 'reason': 'arrived damaged'})
  tool result [error]: Tool 'issue_refund' changes state and was not approved. Do not retry it; ...
Agent: A person must approve this refund first.
Checks: all passed
```

With a key, a person approves refunds in the terminal:

```bash
export ANTHROPIC_API_KEY=...
python examples/support_agent.py "Order A-1002 arrived damaged, please refund 20 USD."
claude-agent verify-audit audit.jsonl    # detects edited, removed or reordered records
claude-agent smoke                       # three live checks against the real API
```

## Use it

```python
from claude_agent import Agent, AgentConfig, AuditLog, Budget, ClientConfig, MessagesClient
from claude_agent.sample_tools import SYSTEM_PROMPT, SupportDesk

desk = SupportDesk()  # synthetic data
agent = Agent(
    MessagesClient(ClientConfig.from_env()),
    desk.registry(approver=lambda tool, args: False),  # your approval policy goes here
    AgentConfig(system=SYSTEM_PROMPT, budget=Budget(max_turns=8, max_tool_calls=10)),
    audit=AuditLog("audit.jsonl"),
)
result = agent.run("What is the status of order A-1001?")
print(result.text, result.terminated_by, result.usage, result.tool_calls)
```

Writing your own tool:

```python
from claude_agent import Tool, ToolRegistry

lookup = Tool(
    name="get_invoice",
    description="Fetch an invoice by id.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "string", "pattern": "^INV-\\d+$"}},
        "required": ["id"],
        "additionalProperties": False,
    },
    handler=lambda args: {"id": args["id"], "total": 12.5},
)

send = Tool(
    name="send_email",
    description="Send an email to a customer.",
    input_schema={
        "type": "object",
        "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
        "required": ["to", "body"],
        "additionalProperties": False,
    },
    handler=lambda args: "queued",
    requires_approval=True,  # does nothing until your approver says yes
)
```

The default model is `claude-sonnet-5-5`. Pin the model you have evaluated, set it in
`AgentConfig(model=...)`, and treat changes as deliberate.

## What has and has not been verified

- **Verified on every push:** ruff, `mypy --strict`, 140+ unit tests with an 85% coverage gate on
  Python 3.11 to 3.13, the 27 harness scenarios, and that a built wheel installs into a clean
  environment and runs the evals on its own. The real `urllib` transport is exercised against a
  loopback server. `pip-audit` and CodeQL run too.
- **Mutation-checked:** each safety mechanism was disabled in turn to confirm a scenario fails
  ([docs/evals.md](docs/evals.md)).
- **Checked against documentation, not against production:** request and response shapes, the
  error model and the Batches endpoints follow Anthropic's public API documentation. I have not
  run this against the live API from CI. `claude-agent smoke` and the manual
  *Live smoke test* workflow do that with your key, for a few cents.
- **Not measured here:** model quality. The scenarios test the harness around the model; see
  [docs/evals.md](docs/evals.md).
- **Prices are placeholders** in `prices.example.toml`. Use your own.
- **Not included:** streaming, async, provider abstraction, memory and multi-agent
  orchestration ([ADR 0001](docs/adr/0001-standard-library-client.md)).
- Written and tested by an AI assistant (Claude) working with the repository owner; the design has not had an independent review.

## Documentation

[Architecture](docs/architecture.md) ·
[Threat model](docs/threat-model.md) ·
[Cost and caching](docs/cost-and-caching.md) ·
[Evaluations](docs/evals.md) ·
[Operations](docs/operations.md) ·
[ADRs](docs/adr)

## Related work

- [agent-guardrails-gateway](https://github.com/darshansatpathy/agent-guardrails-gateway):
  task-scoped grants, taint tracking and a policy enforcement point at the tool boundary.
- [agent-eval-harness](https://github.com/darshansatpathy/agent-eval-harness): scoring model
  behaviour against labelled datasets.
- [agent-tracing-otel](https://github.com/darshansatpathy/agent-tracing-otel): distributed
  tracing for agent runs.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Changes are recorded in
[CHANGELOG.md](CHANGELOG.md). Licensed under the [MIT License](LICENSE).
