# ADR 0005: An audit log that records facts and fingerprints, not content

## Status

Accepted

## Context

Auditors and responders need to know what an agent did: which model, which tools, with what
outcome, at what cost. Prompts and tool results often contain personal or confidential data,
and a log that stores them becomes a second system of record that needs the same protection.

## Decision

Each event is one JSON line with a sequence number, the previous line's hash and its own hash.
By default an event carries a short fingerprint and the length of any content, not the
content. Content logging is opt-in, and a redactor runs on everything either way: credentials
(Anthropic and generic keys, cloud and GitHub tokens, bearer tokens, JWTs), email addresses,
card numbers (with a checksum, so order numbers are not mistaken for cards) and values under
sensitive-looking keys. Redaction happens before hashing.

## Consequences

- Editing, removing or reordering a line breaks verification (`claude-agent verify-audit`).
- Deleting the **tail** of the file is not detectable from the file alone. Store the latest
  hash somewhere the agent cannot write, and ship logs to append-only storage.
- Pattern-based redaction is a safety net, not a guarantee. It will miss secrets with unusual
  shapes. Keep content logging off unless there is a reason and a retention policy.
