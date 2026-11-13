"""The agent loop: call the model, run the tools it asks for, repeat, within hard limits."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent.audit import AuditLog
from claude_agent.budget import Budget, BudgetTracker
from claude_agent.caching import with_cache_control
from claude_agent.client import DEFAULT_MODEL, MessagesClient
from claude_agent.cost import PriceTable
from claude_agent.errors import AgentError, ConfigError
from claude_agent.tools import ToolRegistry
from claude_agent.types import Message, ToolUse, Usage


@dataclass(frozen=True)
class AgentConfig:
    model: str = DEFAULT_MODEL
    system: str = ""
    max_tokens: int = 2048
    temperature: float | None = None
    cache: bool = True
    cache_ttl: str | None = None
    budget: Budget = field(default_factory=Budget)
    tool_choice: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.max_tokens < 1:
            raise ConfigError("max_tokens must be positive")
        if self.temperature is not None and not 0.0 <= self.temperature <= 1.0:
            raise ConfigError("temperature must be between 0 and 1")


@dataclass
class ToolCallRecord:
    name: str
    input: dict[str, Any]
    outcome: str
    output_chars: int
    duration_ms: int


@dataclass
class RunResult:
    run_id: str
    text: str
    terminated_by: str
    turns: int
    usage: Usage
    cost_usd: float | None
    tool_calls: list[ToolCallRecord]
    messages: list[dict[str, Any]]
    request_ids: list[str]

    @property
    def completed(self) -> bool:
        """True only if the model finished on its own, not because a limit or fault stopped it."""
        return self.terminated_by in {"end_turn", "stop_sequence"}


class Agent:
    def __init__(
        self,
        client: MessagesClient,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
        *,
        audit: AuditLog | None = None,
        prices: PriceTable | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.tools = tools
        self.config = config or AgentConfig()
        self.audit = audit or AuditLog()
        self.prices = prices
        self._clock = clock
        if self.config.budget.max_cost_usd is not None:
            if prices is None:
                raise ConfigError("a cost budget needs a price table (see prices.example.toml)")
            prices.price_for(self.config.model)  # fail closed if the model has no price

    # -- public ------------------------------------------------------------------------------

    def run(self, prompt: str | list[dict[str, Any]]) -> RunResult:
        cfg = self.config
        tracker = BudgetTracker(cfg.budget, self._clock)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        records: list[ToolCallRecord] = []
        request_ids: list[str] = []
        seen_calls: dict[str, int] = {}
        last_text = ""
        self.audit.emit(
            "run_start",
            model=cfg.model,
            tools=self.tools.names(),
            prompt=self.audit.content(prompt),
            budget={
                "max_turns": cfg.budget.max_turns,
                "max_tool_calls": cfg.budget.max_tool_calls,
                "max_total_tokens": cfg.budget.max_total_tokens,
                "max_cost_usd": cfg.budget.max_cost_usd,
                "deadline_s": cfg.budget.deadline_s,
            },
        )
        terminated_by = "unknown"
        try:
            terminated_by, last_text = self._loop(
                tracker, messages, records, request_ids, seen_calls
            )
        except AgentError as exc:
            terminated_by = f"error:{type(exc).__name__}"
            self._finish(tracker, terminated_by, records)
            raise
        self._finish(tracker, terminated_by, records)
        return RunResult(
            run_id=self.audit.run_id,
            text=last_text,
            terminated_by=terminated_by,
            turns=tracker.turns,
            usage=tracker.usage,
            cost_usd=tracker.cost_usd if self.prices is not None else None,
            tool_calls=records,
            messages=messages,
            request_ids=request_ids,
        )

    # -- loop --------------------------------------------------------------------------------

    def _loop(
        self,
        tracker: BudgetTracker,
        messages: list[dict[str, Any]],
        records: list[ToolCallRecord],
        request_ids: list[str],
        seen_calls: dict[str, int],
    ) -> tuple[str, str]:
        text = ""
        while True:
            limit = tracker.exhausted()
            if limit:
                return f"budget:{limit}", text
            msg = self.client.create(self._request(messages))
            cost = self._cost(msg)
            tracker.record_turn(msg.usage, cost)
            if msg.request_id:
                request_ids.append(msg.request_id)
            text = msg.text or text
            self.audit.emit(
                "model_call",
                turn=tracker.turns,
                model=msg.model,
                request_id=msg.request_id,
                stop_reason=msg.stop_reason,
                usage=msg.usage.as_dict(),
                cost_usd=round(cost, 6) if self.prices is not None else None,
            )
            messages.append({"role": "assistant", "content": msg.content})

            reason = msg.stop_reason
            if reason in {"end_turn", "stop_sequence"}:
                return str(reason), text
            if reason in {"max_tokens", "refusal"}:
                return str(reason), text
            if reason == "pause_turn":
                continue  # a server-side tool paused the turn; send the history back as is
            if reason != "tool_use":
                return f"unexpected_stop_reason:{reason}", text

            results, hit = self._run_tools(msg.tool_uses, tracker, records, seen_calls)
            messages.append({"role": "user", "content": results})
            if hit:
                return f"budget:{hit}", text

    def _run_tools(
        self,
        uses: list[ToolUse],
        tracker: BudgetTracker,
        records: list[ToolCallRecord],
        seen_calls: dict[str, int],
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Run every requested tool. The API needs one result for each tool_use id."""
        blocks: list[dict[str, Any]] = []
        budget_hit: str | None = None
        for use in uses:
            key = use.name + json.dumps(use.input, sort_keys=True, default=str)
            seen_calls[key] = seen_calls.get(key, 0) + 1

            if tracker.tool_budget_left() == 0:
                budget_hit = "max_tool_calls"
                content, is_error, outcome, ms = "Tool call budget exhausted.", True, "blocked", 0
            elif seen_calls[key] > tracker.budget.max_identical_calls:
                content, is_error, outcome, ms = (
                    f"The identical call to {use.name!r} was repeated "
                    f"{seen_calls[key]} times. Try a different approach or stop.",
                    True,
                    "blocked",
                    0,
                )
            else:
                tracker.record_tool_call()
                result = self.tools.execute(use.name, use.input)
                content, is_error, outcome, ms = (
                    result.content,
                    result.is_error,
                    result.outcome,
                    result.duration_ms,
                )
            records.append(ToolCallRecord(use.name, use.input, outcome, len(content), ms))
            self.audit.emit(
                "tool_call",
                name=use.name,
                outcome=outcome,
                input=self.audit.content(use.input),
                output_chars=len(content),
                duration_ms=ms,
            )
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": use.id,
                "content": content,
            }
            if is_error:
                block["is_error"] = True
            blocks.append(block)
        return blocks, budget_hit

    # -- helpers -----------------------------------------------------------------------------

    def _request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        cfg = self.config
        req: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "messages": messages,
        }
        if cfg.system:
            req["system"] = cfg.system
        defs = self.tools.definitions()
        if defs:
            req["tools"] = defs
            if cfg.tool_choice:
                req["tool_choice"] = cfg.tool_choice
        if cfg.temperature is not None:
            req["temperature"] = cfg.temperature
        if cfg.cache:
            req = with_cache_control(req, ttl=cfg.cache_ttl)
        return req

    def _cost(self, msg: Message) -> float:
        if self.prices is None:
            return 0.0
        return self.prices.cost(self.config.model, msg.usage)

    def _finish(
        self, tracker: BudgetTracker, terminated_by: str, records: list[ToolCallRecord]
    ) -> None:
        self.audit.emit(
            "run_end",
            terminated_by=terminated_by,
            turns=tracker.turns,
            tool_calls=len(records),
            usage=tracker.usage.as_dict(),
            cost_usd=round(tracker.cost_usd, 6) if self.prices is not None else None,
        )
