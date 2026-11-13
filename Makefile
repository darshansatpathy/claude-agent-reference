.PHONY: install check test eval demo smoke audit

install:
	python -m venv .venv && .venv/bin/pip install -e ".[dev]"

check:
	ruff check . && ruff format --check . && mypy && pytest --cov=claude_agent --cov-fail-under=85

test:
	pytest

eval:
	claude-agent eval

demo:
	claude-agent demo

smoke:
	claude-agent smoke

audit:
	pip-audit --skip-editable
