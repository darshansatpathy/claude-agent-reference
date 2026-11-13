"""A support agent on the live API, with a human approving every refund in the terminal.

    export ANTHROPIC_API_KEY=...
    python examples/support_agent.py "Order A-1002 arrived damaged, please refund 20 USD."

The refund tool changes state, so it is registered with ``requires_approval=True``. The approver
below asks a person. Replace it with a ticket, a chat approval or a policy engine in production.
Nothing here touches a real system: the orders are the synthetic desk from ``sample_tools``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from claude_agent import Agent, AgentConfig, AuditLog, Budget, ClientConfig, MessagesClient
from claude_agent.sample_tools import SYSTEM_PROMPT, SupportDesk


def ask_a_person(tool: str, arguments: dict[str, Any]) -> bool:
    print(f"\nApproval needed: {tool} {arguments}")
    return input("Approve? [y/N] ").strip().lower() == "y"


def main() -> int:
    prompt = " ".join(sys.argv[1:]) or "What is the status of order A-1001?"
    desk = SupportDesk()
    agent = Agent(
        MessagesClient(ClientConfig.from_env()),
        desk.registry(approver=ask_a_person),
        AgentConfig(
            system=SYSTEM_PROMPT,
            budget=Budget(max_turns=8, max_tool_calls=10, max_total_tokens=50_000, deadline_s=120),
        ),
        audit=AuditLog(Path("audit.jsonl")),
    )
    result = agent.run(prompt)
    summary = (
        f"{result.terminated_by}; {result.turns} model calls; {result.usage.total_tokens} tokens"
    )
    print(f"\n{result.text}\n\n[{summary}]")
    return 0 if result.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
