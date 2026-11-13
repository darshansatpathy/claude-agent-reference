# Contributing

Thanks for taking the time to improve this project.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make check          # ruff, mypy --strict, pytest with 85% coverage gate
make eval           # 27 hermetic harness scenarios, no network or key needed
```

## What a change needs

- Behaviour of the loop, tools, budgets, retries or audit log is covered by a unit test and, where it is a safety property, by a scripted scenario in `evals/scenarios/`.
- A new scenario states what it checks in `description`. Break the code on purpose once to confirm the scenario fails when it should.
- No new runtime dependency without an ADR. The package is standard-library only on purpose.
- Prices stay in configuration. Never hard-code a rate or present a placeholder as real.
- Python is typed (`mypy --strict`), formatted with `ruff format`, and keeps coverage at or above 85%.
- Update `CHANGELOG.md` for anything a user would notice.

## Pull requests

Keep them focused, describe the behaviour change and how you tested it, and make sure
CI is green. Security issues go through private reporting, see [SECURITY.md](SECURITY.md).
