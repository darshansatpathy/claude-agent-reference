"""Command line: run the harness evals, an offline demo, a live smoke test, verify an audit log."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from claude_agent import __version__
from claude_agent.agent import Agent, AgentConfig
from claude_agent.audit import verify_chain
from claude_agent.budget import Budget
from claude_agent.client import DEFAULT_MODEL, ClientConfig, MessagesClient
from claude_agent.errors import AgentError, ConfigError
from claude_agent.evals import load_scenarios, render, run_all, run_scenario
from claude_agent.sample_tools import SYSTEM_PROMPT, SupportDesk
from claude_agent.tools import deny_all


def _default_scenarios() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "evals" / "scenarios", here.parents[1] / "evals" / "scenarios"):
        if candidate.is_dir():
            return candidate
    raise ConfigError("scenario directory not found; pass --scenarios")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude-agent", description=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eval", help="run the hermetic harness scenarios (no network, no key)")
    e.add_argument("--scenarios", type=Path, default=None)
    e.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    e.add_argument("--only", help="run only scenarios whose name contains this text")

    d = sub.add_parser("demo", help="replay one scenario with a readable transcript (offline)")
    d.add_argument("name", nargs="?", default="refund-denied-without-approval")
    d.add_argument("--scenarios", type=Path, default=None)

    s = sub.add_parser("smoke", help="live check against the real API (needs ANTHROPIC_API_KEY)")
    s.add_argument("--model", default=os.environ.get("CLAUDE_AGENT_MODEL") or DEFAULT_MODEL)

    v = sub.add_parser("verify-audit", help="check that an audit log has not been altered")
    v.add_argument("path", type=Path)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "eval":
            return _eval(args)
        if args.cmd == "demo":
            return _demo(args)
        if args.cmd == "smoke":
            return _smoke(args.model)
        return _verify(args.path)
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _eval(args: argparse.Namespace) -> int:
    directory = args.scenarios or _default_scenarios()
    only = (lambda n: args.only in n) if args.only else None
    results = run_all(directory, only)
    if not results:
        print("no scenarios matched", file=sys.stderr)
        return 2
    sys.stdout.write(render(results, args.format))
    return 0 if all(r.passed for r in results) else 1


def _demo(args: argparse.Namespace) -> int:
    directory = args.scenarios or _default_scenarios()
    specs = [s for s in load_scenarios(directory) if s["name"] == args.name]
    if not specs:
        print(f"no scenario named {args.name!r}", file=sys.stderr)
        return 2
    spec = specs[0]
    result = run_scenario(spec)
    print(f"Scenario: {spec['name']}\n{spec.get('description', '')}\n")
    print(f"User: {spec['prompt']}")
    if result.run:
        for m in result.run.messages[1:]:
            for b in m["content"] if isinstance(m["content"], list) else []:
                kind = b.get("type")
                if kind == "text":
                    print(f"Agent: {b['text']}")
                elif kind == "tool_use":
                    print(f"Agent calls {b['name']}({b['input']})")
                elif kind == "tool_result":
                    flag = " [error]" if b.get("is_error") else ""
                    print(f"  tool result{flag}: {b['content'][:160]}")
        print(f"\nEnded: {result.run.terminated_by} after {result.run.turns} model call(s)")
    print("Checks:", "all passed" if result.passed else "; ".join(result.failures))
    return 0 if result.passed else 1


def _smoke(model: str) -> int:
    """Three live checks. The last one asserts a safety property, not model behaviour."""
    client = MessagesClient(ClientConfig.from_env())
    failures: list[str] = []

    msg = client.create(
        {
            "model": model,
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        }
    )
    print(
        f"1. plain request: stop={msg.stop_reason} tokens={msg.usage.total_tokens} "
        f"request_id={msg.request_id}"
    )
    if not msg.text.strip():
        failures.append("plain request returned no text")

    desk = SupportDesk()
    agent = Agent(
        client,
        desk.registry(deny_all),
        AgentConfig(
            model=model,
            system=SYSTEM_PROMPT,
            max_tokens=512,
            budget=Budget(max_turns=4, max_tool_calls=3, max_total_tokens=20_000, deadline_s=90),
        ),
    )
    run = agent.run("What is the status of order A-1001? Use the lookup tool.")
    used = [c.name for c in run.tool_calls if c.outcome == "ok"]
    print(f"2. tool round trip: ended={run.terminated_by} tools={used}")
    if "lookup_order" not in used or "shipped" not in run.text.lower():
        failures.append("tool round trip did not look up the order and report its status")

    desk = SupportDesk()
    agent = Agent(
        client,
        desk.registry(deny_all),
        AgentConfig(
            model=model,
            system=SYSTEM_PROMPT,
            max_tokens=512,
            budget=Budget(max_turns=4, max_tool_calls=4, max_total_tokens=20_000, deadline_s=90),
        ),
    )
    agent.run("Refund 5 USD on order A-1003, reason: smoke test.")
    refunded = desk.orders["A-1003"]["refunded"]
    print(f"3. unapproved write: refunded={refunded}")
    if refunded != 0.0:
        failures.append("an unapproved refund changed state")

    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    print("smoke test passed" if not failures else "smoke test FAILED")
    return 1 if failures else 0


def _verify(path: Path) -> int:
    ok, bad = verify_chain(path)
    if ok:
        print(f"{path}: chain intact")
        return 0
    print(f"{path}: chain broken at record {bad}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
