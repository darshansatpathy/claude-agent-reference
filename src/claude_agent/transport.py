"""HTTP transport. One small interface so tests and evals can replace the network entirely."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from claude_agent.errors import ConfigError, TransportError


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)  # keys are lower-case


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def validate_base_url(url: str) -> str:
    """Require https. Plain http is accepted only for loopback, for local test servers."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url.rstrip("/")
    if parts.scheme == "http" and parts.hostname in _LOOPBACK:
        return url.rstrip("/")
    raise ConfigError(f"base_url must use https (or http on loopback), got {parts.scheme!r}")


class UrllibTransport:
    """The default transport, built on the standard library. TLS verification is always on."""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        scheme = urllib.parse.urlsplit(url).scheme
        if scheme not in {"https", "http"}:
            raise ConfigError(f"refusing to open a {scheme!r} URL")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
                return HttpResponse(resp.status, resp.read(), _lower(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            finally:
                exc.close()
            return HttpResponse(exc.code, payload, _lower(exc.headers.items()))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc


def _lower(items: object) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in items}  # type: ignore[attr-defined]
