"""Hard limits on a run. An agent loop without them is an unbounded loop with a credit card."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from claude_agent.errors import ConfigError
from claude_agent.types import Usage


@dataclass(frozen=True)
class Budget:
    max_turns: int = 12
    max_tool_calls: int = 30
    max_total_tokens: int = 200_000
    max_cost_usd: float | None = None
    deadline_s: float | None = 300.0
    max_identical_calls: int = 3

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_tool_calls < 0 or self.max_total_tokens < 1:
            raise ConfigError("budget limits must be positive")
        if self.max_identical_calls < 1:
            raise ConfigError("max_identical_calls must be at least 1")
        if self.max_cost_usd is not None and self.max_cost_usd <= 0:
            raise ConfigError("max_cost_usd must be positive")
        if self.deadline_s is not None and self.deadline_s <= 0:
            raise ConfigError("deadline_s must be positive")


class BudgetTracker:
    """Tracks consumption for one run and reports which limit, if any, has been reached."""

    def __init__(self, budget: Budget, clock: Callable[[], float]) -> None:
        self.budget = budget
        self._clock = clock
        self._start = clock()
        self.turns = 0
        self.tool_calls = 0
        self.usage = Usage()
        self.cost_usd = 0.0

    def record_turn(self, usage: Usage, cost: float) -> None:
        self.turns += 1
        self.usage = self.usage + usage
        self.cost_usd += cost

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    def exhausted(self) -> str | None:
        """Name of the first limit that has been reached, checked before each model call."""
        b = self.budget
        if self.turns >= b.max_turns:
            return "max_turns"
        if self.usage.total_tokens >= b.max_total_tokens:
            return "max_total_tokens"
        if b.max_cost_usd is not None and self.cost_usd >= b.max_cost_usd:
            return "max_cost_usd"
        if b.deadline_s is not None and self._clock() - self._start >= b.deadline_s:
            return "deadline"
        return None

    def tool_budget_left(self) -> int:
        return max(0, self.budget.max_tool_calls - self.tool_calls)
