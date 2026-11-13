"""Hermetic evaluations of the agent *harness*.

A scripted transport plays back a recorded sequence of API responses, so each scenario is a
deterministic test of what the loop does with them: whether limits hold, whether an unapproved
write is refused, whether a failed tool is reported rather than hidden. These evals do not
measure model quality (the model is a script). Model behaviour needs the live smoke test and
your own datasets. See docs/evals.md.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from claude_agent.agent import Agent, AgentConfig, RunResult
from claude_agent.audit import AuditLog
from claude_agent.budget import Budget
from claude_agent.caching import count_breakpoints
from claude_agent.client import ClientConfig, MessagesClient
from claude_agent.cost import ModelPrice, PriceTable
from claude_agent.errors import AgentError, TransportError
from claude_agent.sample_tools import SYSTEM_PROMPT, SupportDesk
from claude_agent.transport import HttpResponse

EVAL_MODEL = "claude-sonnet-5-5"
EVAL_KEY = "eval-key-not-a-real-credential"


class ScriptExhausted(AgentError):
    """The agent made more HTTP calls than the scenario scripted."""


@dataclass
class FakeClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def expand_step(step: dict[str, Any], index: int) -> dict[str, Any]:
    """Turn the terse scenario shorthand into an HTTP-level step."""
    if "status" in step or "transport_error" in step:
        return step
    headers = {"request-id": f"req_eval_{index}", **step.get("headers", {})}
    if "error" in step:
        status = int(step["error"])
        kinds = {
            400: "invalid_request_error",
            401: "authentication_error",
            403: "permission_error",
            404: "not_found_error",
            413: "request_too_large",
            429: "rate_limit_error",
            500: "api_error",
            529: "overloaded_error",
        }
        err_body: dict[str, Any] = {
            "type": "error",
            "error": {"type": kinds.get(status, "api_error"), "message": "scripted"},
        }
        return {
            "status": status,
            "headers": headers,
            "body": err_body,
            "advance_s": step.get("advance_s", 0),
        }
    content: list[dict[str, Any]] = []
    if "text" in step:
        content.append({"type": "text", "text": step["text"]})
    for n, use in enumerate(step.get("tool_use", [])):
        content.append(
            {
                "type": "tool_use",
                "id": f"toolu_{index}_{n}",
                "name": use["name"],
                "input": use.get("input", {}),
            }
        )
    stop = step.get("stop_reason") or ("tool_use" if step.get("tool_use") else "end_turn")
    usage = step.get("usage", [100, 20])
    extra = step.get("cache", [0, 0])
    body = {
        "id": f"msg_eval_{index}",
        "type": "message",
        "role": "assistant",
        "model": EVAL_MODEL,
        "content": content,
        "stop_reason": stop,
        "usage": {
            "input_tokens": usage[0],
            "output_tokens": usage[1],
            "cache_creation_input_tokens": extra[0],
            "cache_read_input_tokens": extra[1],
        },
    }
    return {"status": 200, "headers": headers, "body": body, "advance_s": step.get("advance_s", 0)}


class ScriptedTransport:
    def __init__(self, steps: list[dict[str, Any]], clock: FakeClock | None = None) -> None:
        self._steps = deque(expand_step(s, i) for i, s in enumerate(steps))
        self.requests: list[dict[str, Any]] = []
        self.clock = clock or FakeClock()

    def send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body) if body else None,
            }
        )
        if not self._steps:
            raise ScriptExhausted("the agent made more HTTP calls than the scenario scripted")
        step = self._steps.popleft()
        self.clock.now += float(step.get("advance_s", 0))
        if "transport_error" in step:
            raise TransportError(str(step["transport_error"]))
        return HttpResponse(
            int(step["status"]), json.dumps(step["body"]).encode(), step.get("headers", {})
        )


# -- API contract checks applied to every scenario -------------------------------------------


def contract_problems(requests: list[dict[str, Any]]) -> list[str]:
    """Rules the real API enforces, checked on every request the agent sent."""
    problems: list[str] = []
    for n, req in enumerate(r for r in requests if r["url"].endswith("/v1/messages")):
        body = req["body"] or {}
        msgs = body.get("messages", [])
        if not msgs or msgs[0].get("role") != "user":
            problems.append(f"request {n}: conversation must start with a user message")
        for a, b in pairwise(msgs):
            if a.get("role") == b.get("role"):
                problems.append(f"request {n}: two consecutive {a.get('role')} messages")
        for i, m in enumerate(msgs):
            if m.get("role") != "assistant":
                continue
            ids = [
                b["id"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if not ids:
                continue
            nxt = msgs[i + 1] if i + 1 < len(msgs) else None
            content = nxt.get("content") if nxt else None
            if not isinstance(content, list):
                if nxt is not None:
                    problems.append(f"request {n}: tool_use not answered by tool_result blocks")
                continue
            answered = [b.get("tool_use_id") for b in content if b.get("type") == "tool_result"]
            if sorted(answered) != sorted(ids):
                problems.append(
                    f"request {n}: tool_result ids {answered} do not match tool_use ids {ids}"
                )
            kinds = [b.get("type") for b in content]
            if kinds != sorted(kinds, key=lambda k: k != "tool_result"):
                problems.append(f"request {n}: tool_result blocks must come first")
        if count_breakpoints(body) > 4:
            problems.append(f"request {n}: more than 4 cache breakpoints")
        if EVAL_KEY in json.dumps(body):
            problems.append(f"request {n}: API key leaked into the request body")
    return problems


# -- scenarios ---------------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    description: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    run: RunResult | None = None


def _lookup(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = obj[part]
    return obj


def run_scenario(spec: dict[str, Any]) -> ScenarioResult:
    clock = FakeClock()
    transport = ScriptedTransport(spec["script"], clock)
    desk = SupportDesk()
    approved = set(spec.get("approve", []))
    registry = desk.registry(lambda name, _inp: name in approved)
    client = MessagesClient(
        ClientConfig(api_key=EVAL_KEY, max_retries=int(spec.get("max_retries", 3))),
        transport,
        sleep=lambda s: setattr(clock, "now", clock.now + s),
        rand=lambda: 1.0,
    )
    audit_opts = spec.get("audit", {})
    audit = AuditLog(log_content=bool(audit_opts.get("log_content", False)))
    prices = PriceTable({EVAL_MODEL: ModelPrice(1.0, 5.0)})
    budget = Budget(**spec.get("budget", {}))
    agent = Agent(
        client,
        registry,
        AgentConfig(model=EVAL_MODEL, system=SYSTEM_PROMPT, budget=budget),
        audit=audit,
        prices=prices,
        clock=clock,
    )
    expect = spec.get("expect", {})
    failures: list[str] = []
    run: RunResult | None = None
    try:
        run = agent.run(spec["prompt"])
    except AgentError as exc:
        if expect.get("raises") != type(exc).__name__:
            failures.append(f"unexpected {type(exc).__name__}: {exc}")
        elif "raises_status" in expect and getattr(exc, "status", None) != expect["raises_status"]:
            failures.append(
                f"expected status {expect['raises_status']}, got {getattr(exc, 'status', None)}"
            )
    else:
        if "raises" in expect:
            failures.append(f"expected {expect['raises']} but the run completed")
    failures += _check(expect, run, client, transport, desk, audit)
    failures += contract_problems(transport.requests)
    return ScenarioResult(spec["name"], spec.get("description", ""), not failures, failures, run)


def _check(
    expect: dict[str, Any],
    run: RunResult | None,
    client: MessagesClient,
    transport: ScriptedTransport,
    desk: SupportDesk,
    audit: AuditLog,
) -> list[str]:
    out: list[str] = []

    def eq(label: str, want: Any, got: Any) -> None:
        if want != got:
            out.append(f"{label}: expected {want!r}, got {got!r}")

    if "http_calls" in expect:
        eq("http_calls", expect["http_calls"], client.http_calls)
    if "retries" in expect:
        eq("retries", expect["retries"], client.retries)
    if run is not None:
        if "terminated_by" in expect:
            eq("terminated_by", expect["terminated_by"], run.terminated_by)
        if "turns" in expect:
            eq("turns", expect["turns"], run.turns)
        if "tool_outcomes" in expect:
            eq(
                "tool_outcomes",
                [list(x) for x in expect["tool_outcomes"]],
                [[c.name, c.outcome] for c in run.tool_calls],
            )
        for needle in expect.get("text_contains", []):
            if needle.lower() not in run.text.lower():
                out.append(f"final text lacks {needle!r}")
        if "cost_usd" in expect and run.cost_usd is not None:
            eq("cost_usd", expect["cost_usd"], round(run.cost_usd, 6))
        if "cache_read_tokens" in expect:
            eq("cache_read_tokens", expect["cache_read_tokens"], run.usage.cache_read_input_tokens)
    for path, want in expect.get("state", {}).items():
        eq(f"state {path}", want, _lookup({"orders": desk.orders}, path))
    blob = json.dumps(audit.events)
    for secret in expect.get("audit_excludes", []):
        if secret in blob:
            out.append(f"audit log contains {secret!r}")
    for needle in expect.get("audit_includes", []):
        if needle not in blob:
            out.append(f"audit log lacks {needle!r}")
    sent_text = json.dumps([r["body"] for r in transport.requests])
    for needle in expect.get("requests_contain", []):
        if needle not in sent_text:
            out.append(f"no request contained {needle!r}")
    if "request_has_cache_control" in expect:
        sent = [r["body"] for r in transport.requests if r["body"] and "messages" in r["body"]]
        has = bool(sent) and all(count_breakpoints(b) >= 1 for b in sent)
        eq("request_has_cache_control", expect["request_has_cache_control"], has)
    return out


def load_scenarios(directory: Path) -> list[dict[str, Any]]:
    specs = []
    for path in sorted(directory.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec.setdefault("name", path.stem)
        specs.append(spec)
    return specs


def run_all(directory: Path, only: Callable[[str], bool] | None = None) -> list[ScenarioResult]:
    return [run_scenario(s) for s in load_scenarios(directory) if only is None or only(s["name"])]


def render(results: list[ScenarioResult], fmt: str = "text") -> str:
    passed = sum(r.passed for r in results)
    if fmt == "json":
        return json.dumps(
            [{"name": r.name, "passed": r.passed, "failures": r.failures} for r in results],
            indent=2,
        )
    if fmt == "markdown":
        lines = [
            f"## Harness evaluation: {passed}/{len(results)} scenarios passed",
            "",
            "| Scenario | Result | What it checks |",
            "|---|---|---|",
        ]
        for r in results:
            lines.append(f"| `{r.name}` | {'pass' if r.passed else 'FAIL'} | {r.description} |")
        for r in results:
            for f in r.failures:
                lines.append(f"\n- `{r.name}`: {f}")
        return "\n".join(lines) + "\n"
    lines = [f"{'PASS' if r.passed else 'FAIL'}  {r.name}" for r in results]
    for r in results:
        lines += [f"      {r.name}: {f}" for f in r.failures]
    lines.append(f"\n{passed}/{len(results)} scenarios passed")
    return "\n".join(lines) + "\n"
