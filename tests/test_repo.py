"""Invariants about the repository itself: docs match code, links resolve, nothing sensitive."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import claude_agent
from claude_agent import cli

ROOT = Path(__file__).resolve().parents[1]
MD = [
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    *(ROOT / "docs").rglob("*.md"),
]


def test_version_matches_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["version"] == claude_agent.__version__


def test_markdown_links_resolve():
    broken = []
    for f in MD:
        for m in re.finditer(r"\]\(([^)#\s][^)]*)\)", f.read_text()):
            target = m.group(1)
            if target.startswith("http"):
                continue
            if not (f.parent / target.split("#")[0]).resolve().exists():
                broken.append(f"{f.name}: {target}")
    assert broken == []


def test_scenario_count_claims_are_true():
    n = len(list((ROOT / "evals" / "scenarios").glob("*.json")))
    for f in (
        ROOT / "README.md",
        ROOT / "docs" / "evals.md",
        ROOT / "CHANGELOG.md",
        ROOT / "CONTRIBUTING.md",
    ):
        for claimed in re.findall(
            r"\b(\d+) (?:hermetic |scripted )?(?:harness )?scenarios", f.read_text()
        ):
            assert int(claimed) == n, f"{f.name} claims {claimed}, found {n}"


def test_adrs_are_numbered_and_complete():
    adrs = sorted((ROOT / "docs" / "adr").glob("*.md"))
    assert [p.name[:4] for p in adrs] == [f"{i:04d}" for i in range(1, len(adrs) + 1)]
    for p in adrs:
        text = p.read_text()
        for heading in ("## Status", "## Context", "## Decision", "## Consequences"):
            assert heading in text, f"{p.name} lacks {heading}"


def test_every_cli_command_is_documented():
    readme = (ROOT / "README.md").read_text() + (ROOT / "docs" / "evals.md").read_text()
    commands = cli.build_parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    for name in commands:
        assert f"claude-agent {name}" in readme, name


def test_wheel_layout_matches_where_the_cli_looks_for_scenarios():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    force = data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force["evals"] == "claude_agent/evals"
    src = (ROOT / "src" / "claude_agent" / "cli.py").read_text()
    assert 'here / "evals" / "scenarios"' in src


def test_no_credential_shaped_strings_outside_test_fixtures():
    pattern = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{30,}")
    files = [
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and "tests" not in p.parts
        and "scenarios" not in p.parts
        and p.suffix in {".py", ".md", ".toml", ".yml", ".json"}
    ]
    assert [str(p) for p in files if pattern.search(p.read_text())] == []


def test_docs_do_not_present_prices_as_real():
    text = (ROOT / "prices.example.toml").read_text()
    assert "PLACEHOLDER" in text
    assert "$" not in (ROOT / "docs" / "cost-and-caching.md").read_text()


def test_default_model_is_the_documented_one():
    assert claude_agent.client.DEFAULT_MODEL == "claude-sonnet-5-5"
    assert "claude-sonnet-5-5" in (ROOT / "README.md").read_text()


@pytest.mark.parametrize("name", ["ci.yml", "codeql.yml", "live-smoke.yml"])
def test_workflows_exist(name):
    assert (ROOT / ".github" / "workflows" / name).exists()
