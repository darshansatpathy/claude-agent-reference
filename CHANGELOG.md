# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-13

### Added
- Standard-library Messages API client with bounded, jittered retries and `Retry-After` handling.
- Tool-use loop with schema-validated inputs, approval-gated write tools, untrusted-output marking and hard budgets for turns, tool calls, tokens, cost and time.
- Prompt-caching helper, cost estimation from configurable prices, and Message Batches support.
- Redacting, hash-chained audit log with a verification command.
- 27 hermetic harness scenarios with API-contract checks, a demo, and an opt-in live smoke test.

[0.1.0]: https://github.com/darshansatpathy/claude-agent-reference/releases/tag/v0.1.0
