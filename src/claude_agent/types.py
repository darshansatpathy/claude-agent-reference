"""Typed views over API responses.

Content blocks stay plain dictionaries on purpose. The API expects the assistant's blocks to
be sent back unchanged on the next turn (including block types this code does not know
about), so the safest representation is the one that cannot lose fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Usage:
        raw = raw or {}
        return cls(
            input_tokens=int(raw.get("input_tokens") or 0),
            output_tokens=int(raw.get("output_tokens") or 0),
            cache_creation_input_tokens=int(raw.get("cache_creation_input_tokens") or 0),
            cache_read_input_tokens=int(raw.get("cache_read_input_tokens") or 0),
        )

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
        )

    @property
    def total_tokens(self) -> int:
        """All tokens processed, including cache reads and writes."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Message:
    id: str
    model: str
    content: list[dict[str, Any]]
    stop_reason: str | None
    usage: Usage
    request_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], request_id: str | None = None) -> Message:
        content = raw.get("content")
        return cls(
            id=str(raw.get("id", "")),
            model=str(raw.get("model", "")),
            content=[b for b in content if isinstance(b, dict)]
            if isinstance(content, list)
            else [],
            stop_reason=raw.get("stop_reason"),
            usage=Usage.from_dict(raw.get("usage")),
            request_id=request_id,
        )

    @property
    def text(self) -> str:
        return "".join(str(b.get("text", "")) for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[ToolUse]:
        uses: list[ToolUse] = []
        for b in self.content:
            if b.get("type") == "tool_use":
                raw_input = b.get("input")
                uses.append(
                    ToolUse(
                        id=str(b.get("id", "")),
                        name=str(b.get("name", "")),
                        input=raw_input if isinstance(raw_input, dict) else {},
                    )
                )
        return uses
