from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_agent import cli
from claude_agent.audit import AuditLog, verify_chain
from claude_agent.evals import (
    ScriptedTransport,
    ScriptExhausted,
    contract_problems,
    expand_step,
    load_scenarios,
    render,
    run_all,
    run_scenario,
)

SCENARIOS = Path(__file__).resolve().parents[1] / "evals" / "scenarios"


def test_every_shipped_scenario_passes():
    results = run_all(SCENARIOS)
    assert len(results) >= 25
    assert [r.name for r in results if not r.passed] == []


def test_scenarios_are_well_formed_and_uniquely_named():
    specs = load_scenarios(SCENARIOS)
    names = [s["name"] for s in specs]
    assert len(names) == len(set(names))
    for s in specs:
        assert s["description"] and s["prompt"] and s["script"] and s["expect"], s["name"]


def test_a_failing_expectation_is_reported():
    spec = {
        "name": "x",
        "prompt": "hi",
        "script": [{"text": "hello"}],
        "expect": {"terminated_by": "refusal", "text_contains": ["zzz"]},
    }
    res = run_scenario(spec)
    assert not res.passed and len(res.failures) == 2


def test_unexpected_exception_fails_a_scenario():
    spec = {"name": "x", "prompt": "hi", "script": [{"error": 401}], "expect": {}}
    res = run_scenario(spec)
    assert not res.passed and "ApiError" in res.failures[0]


def test_expected_exception_that_does_not_happen_fails():
    spec = {
        "name": "x",
        "prompt": "hi",
        "script": [{"text": "ok"}],
        "expect": {"raises": "ApiError"},
    }
    assert not run_scenario(spec).passed


def test_wrong_status_for_expected_exception_fails():
    spec = {
        "name": "x",
        "prompt": "hi",
        "script": [{"error": 401}],
        "expect": {"raises": "ApiError", "raises_status": 403},
    }
    assert not run_scenario(spec).passed


def test_script_exhaustion_is_an_error_not_a_hang():
    t = ScriptedTransport([])
    with pytest.raises(ScriptExhausted):
        t.send("POST", "https://x", {}, b"{}", 1.0)


def test_expand_step_shorthand():
    s = expand_step(
        {
            "text": "a",
            "tool_use": [{"name": "n", "input": {"k": 1}}],
            "usage": [5, 6],
            "cache": [1, 2],
        },
        3,
    )
    body = s["body"]
    assert body["stop_reason"] == "tool_use" and body["content"][1]["id"] == "toolu_3_0"
    assert body["usage"] == {
        "input_tokens": 5,
        "output_tokens": 6,
        "cache_creation_input_tokens": 1,
        "cache_read_input_tokens": 2,
    }
    raw = {"status": 200, "body": {}}
    assert expand_step(raw, 0) is raw
    assert expand_step({"error": 599}, 0)["body"]["error"]["type"] == "api_error"


def _req(messages, **extra):
    return [
        {"url": "https://api.anthropic.com/v1/messages", "body": {"messages": messages, **extra}}
    ]


def test_contract_checker_catches_each_kind_of_violation():
    use = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}}],
    }
    good = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}],
    }
    u = {"role": "user", "content": "hi"}
    assert contract_problems(_req([u, use, good])) == []
    assert any("start with a user" in p for p in contract_problems(_req([use])))
    assert any("consecutive" in p for p in contract_problems(_req([u, u])))
    wrong = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "other", "content": "x"}],
    }
    assert any("do not match" in p for p in contract_problems(_req([u, use, wrong])))
    late = {"role": "user", "content": [{"type": "text", "text": "x"}, *good["content"]]}
    assert any("come first" in p for p in contract_problems(_req([u, use, late])))
    assert any(
        "not answered" in p
        for p in contract_problems(_req([u, use, {"role": "assistant", "content": "x"}]))
    )
    marked = [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}] * 5
    assert any(
        "cache breakpoints" in p
        for p in contract_problems(_req([{"role": "user", "content": marked}]))
    )


def test_render_formats():
    results = run_all(SCENARIOS, only=lambda n: n.startswith("simple"))
    assert "1/1 scenarios passed" in render(results)
    assert render(results, "markdown").startswith("## Harness evaluation: 1/1")
    assert json.loads(render(results, "json"))[0]["passed"] is True


def test_cli_eval_and_filters(capsys):
    assert cli.main(["eval"]) == 0
    assert "scenarios passed" in capsys.readouterr().out
    assert cli.main(["eval", "--only", "budget", "--format", "markdown"]) == 0
    assert cli.main(["eval", "--only", "no-such-scenario"]) == 2


def test_cli_eval_fails_when_a_scenario_fails(tmp_path, capsys):
    bad = {
        "name": "bad",
        "description": "d",
        "prompt": "hi",
        "script": [{"text": "x"}],
        "expect": {"turns": 9},
    }
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    assert cli.main(["eval", "--scenarios", str(tmp_path)]) == 1
    assert "FAIL  bad" in capsys.readouterr().out


def test_cli_demo(capsys):
    assert cli.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "issue_refund" in out and "was not approved" in out and "Checks: all passed" in out
    assert cli.main(["demo", "no-such"]) == 2


def test_cli_verify_audit(tmp_path, capsys):
    path = tmp_path / "a.jsonl"
    log = AuditLog(path)
    log.emit("a")
    log.emit("b")
    assert cli.main(["verify-audit", str(path)]) == 0
    path.write_text(path.read_text().replace('"event": "a"', '"event": "z"'))
    assert verify_chain(path)[0] is False
    assert cli.main(["verify-audit", str(path)]) == 1


def test_cli_smoke_without_a_key_is_a_clean_error(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["smoke"]) == 2
    assert "API key" in capsys.readouterr().err


def test_cli_smoke_against_a_scripted_model(monkeypatch, capsys):
    """Exercise the smoke flow end to end with scripted responses instead of the network."""
    from claude_agent import client as client_mod

    use = {"text": "", "tool_use": [{"name": "lookup_order", "input": {"order_id": "A-1001"}}]}
    refund = {
        "tool_use": [
            {
                "name": "issue_refund",
                "input": {"order_id": "A-1003", "amount": 5, "reason": "smoke test"},
            }
        ]
    }
    script = [
        {"text": "ready"},
        use,
        {"text": "Order A-1001 has shipped."},
        refund,
        {"text": "A person must approve."},
    ]
    transport = ScriptedTransport(script)
    real = client_mod.MessagesClient

    def factory(config):
        return real(config, transport, sleep=lambda s: None)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real-123")
    monkeypatch.setattr(cli, "MessagesClient", factory)
    assert cli.main(["smoke"]) == 0
    assert "smoke test passed" in capsys.readouterr().out


def test_cli_smoke_reports_failures(monkeypatch, capsys):
    from claude_agent import client as client_mod

    transport = ScriptedTransport([{"text": ""}, {"text": "I don't know."}, {"text": "No."}])
    real = client_mod.MessagesClient
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real-123")
    monkeypatch.setattr(
        cli, "MessagesClient", lambda config: real(config, transport, sleep=lambda s: None)
    )
    assert cli.main(["smoke"]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "0.1.0" in capsys.readouterr().out
