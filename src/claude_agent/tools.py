"""Tool registry: validated inputs, bounded outputs, approval for anything with side effects."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent.errors import ConfigError
from claude_agent.schema import validate

Handler = Callable[[dict[str, Any]], Any]
Approver = Callable[[str, dict[str, Any]], bool]


def deny_all(_name: str, _input: dict[str, Any]) -> bool:
    """The default approver. Side-effecting tools do nothing until a human policy says yes."""
    return False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler = field(repr=False)
    requires_approval: bool = False
    untrusted_output: bool = False
    max_output_chars: int = 20_000

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 64 or not _valid_name(self.name):
            raise ConfigError(f"invalid tool name {self.name!r} (letters, digits, _ and - only)")
        if not self.description.strip():
            raise ConfigError(f"tool {self.name!r} needs a description")
        if self.input_schema.get("type") != "object":
            raise ConfigError(f"tool {self.name!r}: input_schema must have type 'object'")
        if self.max_output_chars < 100:
            raise ConfigError("max_output_chars must be at least 100")

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def _valid_name(name: str) -> bool:
    return all(c.isalnum() or c in "_-" for c in name)


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool
    outcome: str  # ok | error | invalid | denied | unknown | blocked
    duration_ms: int = 0


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None, approver: Approver = deny_all) -> None:
        self._tools: dict[str, Tool] = {}
        self.approver = approver
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ConfigError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        return [self._tools[n].definition() for n in sorted(self._tools)]

    def execute(self, name: str, raw_input: dict[str, Any]) -> ToolResult:
        """Run one tool call. Never raises: every failure becomes a result the model can read."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                f"Unknown tool {name!r}. Available: {', '.join(self.names())}.", True, "unknown"
            )
        problems = validate(raw_input, tool.input_schema)
        if problems:
            return ToolResult("Invalid input: " + "; ".join(problems[:5]), True, "invalid")
        if tool.requires_approval and not self.approver(name, raw_input):
            return ToolResult(
                f"Tool {name!r} changes state and was not approved. Do not retry it; tell the "
                "user that a person must approve this action.",
                True,
                "denied",
            )
        started = time.perf_counter()
        try:
            result = tool.handler(raw_input)
        except Exception as exc:  # a tool bug must not crash the run
            ms = int((time.perf_counter() - started) * 1000)
            return ToolResult(f"Tool {name!r} failed: {type(exc).__name__}", True, "error", ms)
        ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(self._render(tool, result), False, "ok", ms)

    @staticmethod
    def _render(tool: Tool, result: Any) -> str:
        text = (
            result if isinstance(result, str) else json.dumps(result, default=str, sort_keys=True)
        )
        if len(text) > tool.max_output_chars:
            omitted = len(text) - tool.max_output_chars
            text = text[: tool.max_output_chars] + f"\n[truncated {omitted} characters]"
        if tool.untrusted_output:
            text = wrap_untrusted(tool.name, text)
        return text


def wrap_untrusted(source: str, text: str) -> str:
    """Mark content from outside the trust boundary so the model can treat it as data.

    This is a mitigation, not a guarantee. The control that holds is that side-effecting tools
    need approval no matter what the model has read.
    """
    safe = text.replace("</untrusted>", "<\\/untrusted>")
    return f'<untrusted source="{source}">\n{safe}\n</untrusted>'
