"""A Messages API client with bounded retries, honest errors and no hidden state.

Why not the official SDK? See docs/adr/0001. In short: this repository shows what a production
client has to do, and the standard library keeps that readable and auditable.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent.errors import ApiError, ConfigError, TransportError
from claude_agent.transport import HttpResponse, Transport, UrllibTransport, validate_base_url
from claude_agent.types import Message

DEFAULT_MODEL = "claude-sonnet-5-5"
API_VERSION = "2023-06-01"


@dataclass(frozen=True)
class ClientConfig:
    api_key: str = field(repr=False)
    base_url: str = "https://api.anthropic.com"
    api_version: str = API_VERSION
    timeout_s: float = 120.0
    max_retries: int = 3
    backoff_base_s: float = 0.5
    backoff_cap_s: float = 20.0
    max_retry_after_s: float = 60.0
    user_agent: str = "claude-agent-reference/0.1"

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_key.strip():
            raise ConfigError("an API key is required (set ANTHROPIC_API_KEY)")
        if "\n" in self.api_key or "\r" in self.api_key:
            raise ConfigError("the API key contains a line break")
        validate_base_url(self.base_url)
        if self.max_retries < 0 or self.timeout_s <= 0:
            raise ConfigError("max_retries must be >= 0 and timeout_s > 0")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ClientConfig:
        e = os.environ if env is None else env
        key = e.get("ANTHROPIC_API_KEY", "")
        base = e.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        return cls(api_key=key, base_url=base)


class MessagesClient:
    def __init__(
        self,
        config: ClientConfig,
        transport: Transport | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.config = config
        self._transport: Transport = transport or UrllibTransport()
        self._sleep = sleep
        self._rand = rand
        self.http_calls = 0
        self.retries = 0

    # -- public API -------------------------------------------------------------------------

    def create(self, request: dict[str, Any]) -> Message:
        """Send one Messages request and return the parsed response."""
        resp = self.request("POST", "/v1/messages", request)
        data = self._json(resp)
        return Message.from_dict(data, request_id=resp.headers.get("request-id"))

    def request(
        self,
        method: str,
        path_or_url: str,
        payload: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Send with retries. Returns only 2xx responses and raises ``ApiError`` otherwise."""
        url = (
            path_or_url
            if path_or_url.startswith(("https://", "http://"))
            else self.config.base_url.rstrip("/") + path_or_url
        )
        self._check_same_origin(url)
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": self.config.api_version,
            "content-type": "application/json",
            "user-agent": self.config.user_agent,
        }
        attempt = 0
        while True:
            error: ApiError | TransportError
            try:
                self.http_calls += 1
                resp = self._transport.send(method, url, headers, body, self.config.timeout_s)
            except TransportError as exc:
                error = exc
            else:
                if 200 <= resp.status < 300:
                    return resp
                error = self._to_api_error(resp)
            if not self._should_retry(error, attempt):
                raise error
            self.retries += 1
            self._sleep(self._delay(error, attempt))
            attempt += 1

    # -- internals --------------------------------------------------------------------------

    def _check_same_origin(self, url: str) -> None:
        """Never send the API key to a host other than the configured one."""
        base = validate_base_url(self.config.base_url)
        if not (url == base or url.startswith(base + "/")):
            raise ConfigError("refusing to send credentials to a URL outside base_url")

    @staticmethod
    def _json(resp: HttpResponse) -> dict[str, Any]:
        try:
            data = json.loads(resp.body)
        except json.JSONDecodeError as exc:
            raise ApiError(
                resp.status,
                "invalid_response",
                "response body is not JSON",
                request_id=resp.headers.get("request-id"),
            ) from exc
        if not isinstance(data, dict):
            raise ApiError(resp.status, "invalid_response", "response body is not an object")
        return data

    @staticmethod
    def _to_api_error(resp: HttpResponse) -> ApiError:
        error_type, message, request_id = "api_error", f"HTTP {resp.status}", None
        try:
            data = json.loads(resp.body)
            if isinstance(data, dict):
                inner = data.get("error")
                if isinstance(inner, dict):
                    error_type = str(inner.get("type", error_type))
                    message = str(inner.get("message", message))
                rid = data.get("request_id")
                request_id = str(rid) if rid else None
        except json.JSONDecodeError:
            message = f"HTTP {resp.status} with a non-JSON body"
        request_id = request_id or resp.headers.get("request-id")
        return ApiError(
            resp.status,
            error_type,
            message,
            request_id=request_id,
            retry_after=_parse_retry_after(resp.headers.get("retry-after")),
        )

    def _should_retry(self, error: ApiError | TransportError, attempt: int) -> bool:
        if attempt >= self.config.max_retries:
            return False
        if isinstance(error, TransportError):
            return True
        if not error.retryable:
            return False
        # A 429 that asks us to wait longer than we are willing to is surfaced, not slept on.
        return not (
            error.retry_after is not None and error.retry_after > self.config.max_retry_after_s
        )

    def _delay(self, error: ApiError | TransportError, attempt: int) -> float:
        ceiling = min(self.config.backoff_cap_s, self.config.backoff_base_s * (2.0**attempt))
        jittered = ceiling * self._rand()  # full jitter
        if isinstance(error, ApiError) and error.retry_after is not None:
            return max(error.retry_after, jittered)
        return jittered


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None  # HTTP-date form is not used by this API; ignore rather than guess
    return seconds if seconds >= 0 else None
