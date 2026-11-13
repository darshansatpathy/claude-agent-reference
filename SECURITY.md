# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/darshansatpathy/claude-agent-reference/security/advisories/new)
for this repository. You should get an acknowledgement within three working days.

## Scope

In scope: the client, the agent loop, tool execution and approval, budgets, the audit log and the evaluation harness. Out of scope: the model's own behaviour, the Claude API service, and the tools and approval policy you build on top of this reference.

## Operating it safely

- Keep `ANTHROPIC_API_KEY` in your secret manager or environment. The library never logs it and sends it only to the configured `base_url`.
- Leave `log_content` off in the audit log unless you have a retention and access policy for prompts and tool results.
- Anything with side effects must be registered with `requires_approval=True` and wired to a real approver. The default denies.
- Treat tool output that came from outside your company as untrusted; mark such tools `untrusted_output=True`.
- Ship the audit log to storage the agent cannot write to, and store the latest chain hash somewhere separate to detect truncation.
