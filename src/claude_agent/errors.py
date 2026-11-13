"""Exception types. Every error that can leave the library is an ``AgentError``."""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all errors raised by this package."""


class ConfigError(AgentError):
    """The configuration is missing or invalid. Raised before any request is sent."""


class TransportError(AgentError):
    """The request never produced an HTTP response (DNS, TLS, connection reset, timeout)."""


class ApiError(AgentError):
    """The API answered with a non-success status."""

    def __init__(
        self,
        status: int,
        error_type: str,
        message: str,
        *,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{status} {error_type}: {message}")
        self.status = status
        self.error_type = error_type
        self.message = message
        self.request_id = request_id
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        """Transient failures are worth retrying. Request, auth and size errors are not."""
        return self.status in {408, 409, 429, 500, 502, 503, 504, 529}


class BatchError(AgentError):
    """A Message Batch could not be created, completed or read."""
