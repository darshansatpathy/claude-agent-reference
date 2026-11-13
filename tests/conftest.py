from __future__ import annotations

import json
from typing import Any

import pytest

from claude_agent.client import ClientConfig, MessagesClient
from claude_agent.errors import TransportError
from claude_agent.transport import HttpResponse

KEY = "test-key-not-real-123456"


class FakeTransport:
    """Returns queued responses (or raises queued errors) and records every request."""

    def __init__(self, *items: HttpResponse | Exception) -> None:
        self.items = list(items)
        self.calls: list[dict[str, Any]] = []

    def send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "body": body, "timeout": timeout}
        )
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(body: dict[str, Any], **headers: str) -> HttpResponse:
    return HttpResponse(
        200, json.dumps(body).encode(), {k.replace("_", "-"): v for k, v in headers.items()}
    )


def err(
    status: int, kind: str = "api_error", message: str = "boom", **headers: str
) -> HttpResponse:
    body = {"type": "error", "error": {"type": kind, "message": message}}
    return HttpResponse(
        status, json.dumps(body).encode(), {k.replace("_", "-"): v for k, v in headers.items()}
    )


def message(
    text: str = "hi",
    stop: str = "end_turn",
    usage: tuple[int, int] = (10, 5),
    content: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content if content is not None else [{"type": "text", "text": text}],
        "stop_reason": stop,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
    }


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_client(sleeps: list[float]):  # type: ignore[no-untyped-def]
    def _make(*items: HttpResponse | Exception, **cfg: Any) -> tuple[MessagesClient, FakeTransport]:
        t = FakeTransport(*items)
        c = MessagesClient(
            ClientConfig(api_key=KEY, **cfg), t, sleep=sleeps.append, rand=lambda: 1.0
        )
        return c, t

    return _make


__all__ = ["KEY", "FakeTransport", "TransportError", "err", "message", "ok"]
