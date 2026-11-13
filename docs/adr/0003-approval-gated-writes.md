# ADR 0003: Side effects need approval, and the default is no

## Status

Accepted

## Context

Prompt injection means a model can be made to call any tool it can see, by text it read while
doing legitimate work. Asking the model to refuse is a mitigation, not a control: it is the
component that has been fooled.

## Decision

Tools that change state are registered with `requires_approval=True`. Execution calls an
`approver(tool, arguments)` supplied by the application, and the default approver denies. The
refusal is returned to the model as an error result that tells it not to retry and to say a
person must approve. Tools that return text from outside the company are marked
`untrusted_output=True`, which truncates the output and wraps it in `<untrusted>` tags (with
the closing tag escaped), and the system prompt tells the model to treat that content as
data.

The approval, not the wrapper, is the control. The wrapper lowers the odds that the model is
fooled; the approval means that being fooled does not cause harm.

## Consequences

- The scenario `injection-in-document-cannot-refund` has the scripted model obey a malicious
  document and shows that no refund happens.
- Approval adds latency and needs a real approver. The example prompts in the terminal; a
  service would use a ticket, a chat message or a policy engine, and should show the exact
  arguments to the person.
- Read tools are not gated. Scope them to the task; see the threat model.
