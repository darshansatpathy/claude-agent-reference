from __future__ import annotations

import json

import pytest

from claude_agent.agent import Agent, AgentConfig
from claude_agent.audit import AuditLog
from claude_agent.budget import Budget, BudgetTracker
from claude_agent.cost import ModelPrice, PriceTable
from claude_agent.errors import ApiError, ConfigError
from claude_agent.sample_tools import SupportDesk
from claude_agent.tools import Tool, ToolRegistry
from claude_agent.types import Usage
from tests.conftest import err, message, ok

TOOL_USE = [
    {"type": "tool_use", "id": "t1", "name": "lookup_order", "input": {"order_id": "A-1001"}}
]


def make_agent(client, **kw):
    cfg = kw.pop("config", AgentConfig(system="sys"))
    return Agent(client, SupportDesk().registry(), cfg, **kw)


def test_plain_run_result_fields(make_client):
    c, _ = make_client(ok(message("hello", usage=(10, 5)), request_id="req_1"))
    prices = PriceTable({"claude-sonnet-5-5": ModelPrice(1.0, 5.0)})
    r = make_agent(c, prices=prices).run("hi")
    assert r.completed and r.text == "hello" and r.turns == 1 and r.request_ids == ["req_1"]
    assert r.usage.total_tokens == 15 and r.cost_usd == pytest.approx(10e-6 + 5 * 5e-6)
    assert r.messages[0] == {"role": "user", "content": "hi"}


def test_cost_is_none_without_prices(make_client):
    c, _ = make_client(ok(message()))
    assert make_agent(c).run("hi").cost_usd is None


def test_request_shape_has_cache_system_tools_and_sampling(make_client):
    c, t = make_client(ok(message()))
    cfg = AgentConfig(system="sys", temperature=0.2, max_tokens=99, tool_choice={"type": "auto"})
    make_agent(c, config=cfg).run("hi")
    body = json.loads(t.calls[0]["body"])
    assert (
        body["model"] == "claude-sonnet-5-5"
        and body["max_tokens"] == 99
        and body["temperature"] == 0.2
    )
    assert body["system"][0]["cache_control"] and body["tools"][-1]["cache_control"]
    assert body["tool_choice"] == {"type": "auto"}
    assert [x["name"] for x in body["tools"]] == ["issue_refund", "lookup_order", "search_kb"]


def test_cache_can_be_disabled(make_client):
    c, t = make_client(ok(message()))
    make_agent(c, config=AgentConfig(system="sys", cache=False)).run("hi")
    assert "cache_control" not in t.calls[0]["body"].decode()


def test_no_tools_means_no_tools_field(make_client):
    c, t = make_client(ok(message()))
    Agent(c, ToolRegistry(), AgentConfig(tool_choice={"type": "auto"})).run("hi")
    body = json.loads(t.calls[0]["body"])
    assert "tools" not in body and "tool_choice" not in body and "system" not in body


def test_history_is_stored_without_cache_markers(make_client):
    c, _ = make_client(ok(message(stop="tool_use", content=TOOL_USE)), ok(message("done")))
    r = make_agent(c).run("status?")
    assert "cache_control" not in json.dumps(r.messages)
    assert r.messages[2]["content"][0]["type"] == "tool_result"


def test_assistant_blocks_are_replayed_unchanged(make_client):
    blocks = [{"type": "thinking", "thinking": "hmm", "signature": "sig"}, *TOOL_USE]
    c, t = make_client(ok(message(stop="tool_use", content=blocks)), ok(message("done")))
    make_agent(c).run("status?")
    second = json.loads(t.calls[1]["body"])
    assert second["messages"][1]["content"][0] == blocks[0]


def test_api_error_propagates_and_run_end_is_audited(make_client):
    c, _ = make_client(err(400, "invalid_request_error"))
    audit = AuditLog()
    with pytest.raises(ApiError):
        make_agent(c, audit=audit).run("hi")
    assert [e["event"] for e in audit.events] == ["run_start", "run_end"]
    assert audit.events[-1]["terminated_by"] == "error:ApiError"


def test_audit_trail_for_a_tool_run(make_client):
    c, _ = make_client(ok(message(stop="tool_use", content=TOOL_USE)), ok(message("done")))
    audit = AuditLog()
    make_agent(c, audit=audit).run("status of A-1001")
    kinds = [e["event"] for e in audit.events]
    assert kinds == ["run_start", "model_call", "tool_call", "model_call", "run_end"]
    tool_event = audit.events[2]
    assert tool_event["name"] == "lookup_order" and tool_event["outcome"] == "ok"
    assert "content" not in tool_event["input"]  # fingerprint only by default
    assert audit.events[-1]["terminated_by"] == "end_turn"


def test_cost_budget_requires_a_price_for_the_model(make_client):
    c, _ = make_client()
    cfg = AgentConfig(budget=Budget(max_cost_usd=1.0))
    with pytest.raises(ConfigError):
        make_agent(c, config=cfg)
    with pytest.raises(ConfigError):
        make_agent(c, config=cfg, prices=PriceTable({"other-model": ModelPrice(1, 1)}))


def test_tool_use_missing_a_tool_result_budget_hit_still_answers_every_id(make_client):
    two = [*TOOL_USE, {**TOOL_USE[0], "id": "t2", "input": {"order_id": "A-1002"}}]
    c, _ = make_client(ok(message(stop="tool_use", content=two)))
    r = make_agent(c, config=AgentConfig(budget=Budget(max_tool_calls=1))).run("both")
    results = r.messages[-1]["content"]
    assert [b["tool_use_id"] for b in results] == ["t1", "t2"]
    assert results[1]["is_error"] and r.terminated_by == "budget:max_tool_calls"


def test_agent_config_validation():
    with pytest.raises(ConfigError):
        AgentConfig(max_tokens=0)
    with pytest.raises(ConfigError):
        AgentConfig(temperature=2.0)


def test_budget_validation():
    for bad in (
        {"max_turns": 0},
        {"max_total_tokens": 0},
        {"max_identical_calls": 0},
        {"max_cost_usd": 0},
        {"deadline_s": 0},
        {"max_tool_calls": -1},
    ):
        with pytest.raises(ConfigError):
            Budget(**bad)


def test_budget_tracker_reports_first_exhausted_limit():
    now = [0.0]
    tr = BudgetTracker(
        Budget(max_turns=2, max_total_tokens=100, max_cost_usd=1.0, deadline_s=5), lambda: now[0]
    )
    assert tr.exhausted() is None
    tr.record_turn(Usage(input_tokens=60, output_tokens=50), 0.1)
    assert tr.exhausted() == "max_total_tokens"
    tr2 = BudgetTracker(Budget(max_cost_usd=0.5), lambda: now[0])
    tr2.record_turn(Usage(), 0.6)
    assert tr2.exhausted() == "max_cost_usd"
    tr3 = BudgetTracker(Budget(deadline_s=5), lambda: now[0])
    now[0] = 6
    assert tr3.exhausted() == "deadline"
    tr3.record_tool_call()
    assert tr3.tool_budget_left() == Budget().max_tool_calls - 1


def test_tool_registry_integration_with_custom_tool(make_client):
    reg = ToolRegistry(
        [Tool("echo", "echo", {"type": "object", "properties": {}}, lambda a: "pong")]
    )
    use = [{"type": "tool_use", "id": "e1", "name": "echo", "input": {}}]
    c, _ = make_client(ok(message(stop="tool_use", content=use)), ok(message("pong received")))
    r = Agent(c, reg).run("ping")
    assert r.tool_calls[0].output_chars == 4 and r.text == "pong received"
