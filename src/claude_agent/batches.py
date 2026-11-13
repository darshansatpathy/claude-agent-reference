"""Message Batches: asynchronous bulk requests at a discount, for work nobody is waiting on."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent.client import MessagesClient
from claude_agent.cost import PriceTable
from claude_agent.errors import BatchError, ConfigError
from claude_agent.types import Message, Usage

_CUSTOM_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_BATCH_ID = re.compile(r"^[A-Za-z0-9_]{1,128}$")
MAX_REQUESTS = 100_000


@dataclass(frozen=True)
class BatchRequest:
    custom_id: str
    params: dict[str, Any]


@dataclass(frozen=True)
class BatchInfo:
    id: str
    processing_status: str
    request_counts: dict[str, int] = field(default_factory=dict)
    results_url: str | None = None

    @property
    def ended(self) -> bool:
        return self.processing_status == "ended"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BatchInfo:
        counts = raw.get("request_counts")
        return cls(
            id=str(raw.get("id", "")),
            processing_status=str(raw.get("processing_status", "")),
            request_counts={k: int(v) for k, v in counts.items()}
            if isinstance(counts, dict)
            else {},
            results_url=raw.get("results_url"),
        )


@dataclass(frozen=True)
class BatchResult:
    custom_id: str
    type: str  # succeeded | errored | canceled | expired
    message: Message | None = None
    error: dict[str, Any] | None = None


class BatchClient:
    def __init__(
        self,
        client: MessagesClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self._sleep = sleep
        self._clock = clock

    def create(self, requests: list[BatchRequest]) -> BatchInfo:
        if not requests:
            raise ConfigError("a batch needs at least one request")
        if len(requests) > MAX_REQUESTS:
            raise ConfigError(f"a batch holds at most {MAX_REQUESTS} requests")
        seen: set[str] = set()
        for r in requests:
            if not _CUSTOM_ID.match(r.custom_id):
                raise ConfigError(f"custom_id {r.custom_id!r} must match [A-Za-z0-9_-]{{1,64}}")
            if r.custom_id in seen:
                raise ConfigError(f"duplicate custom_id {r.custom_id!r}")
            seen.add(r.custom_id)
        payload = {"requests": [{"custom_id": r.custom_id, "params": r.params} for r in requests]}
        resp = self.client.request("POST", "/v1/messages/batches", payload)
        return BatchInfo.from_dict(self.client._json(resp))

    def get(self, batch_id: str) -> BatchInfo:
        resp = self.client.request("GET", f"/v1/messages/batches/{self._safe(batch_id)}")
        return BatchInfo.from_dict(self.client._json(resp))

    def cancel(self, batch_id: str) -> BatchInfo:
        resp = self.client.request(
            "POST", f"/v1/messages/batches/{self._safe(batch_id)}/cancel", {}
        )
        return BatchInfo.from_dict(self.client._json(resp))

    def wait(
        self, batch_id: str, *, poll_s: float = 30.0, timeout_s: float = 86_400.0
    ) -> BatchInfo:
        """Poll until the batch ends. Raises ``BatchError`` if it does not within ``timeout_s``."""
        start = self._clock()
        while True:
            info = self.get(batch_id)
            if info.ended:
                return info
            if self._clock() - start >= timeout_s:
                raise BatchError(
                    f"batch {batch_id} still {info.processing_status} after {timeout_s}s"
                )
            self._sleep(poll_s)

    def results(self, info: BatchInfo) -> list[BatchResult]:
        if not info.ended or not info.results_url:
            raise BatchError("results are available only after the batch has ended")
        resp = self.client.request("GET", info.results_url)
        out: list[BatchResult] = []
        for line in resp.body.decode("utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            result = raw.get("result", {})
            kind = str(result.get("type", ""))
            message = Message.from_dict(result["message"]) if kind == "succeeded" else None
            error = result.get("error") if kind == "errored" else None
            out.append(BatchResult(str(raw.get("custom_id", "")), kind, message, error))
        return out

    @staticmethod
    def _safe(batch_id: str) -> str:
        if not _BATCH_ID.match(batch_id):
            raise ConfigError("invalid batch id")
        return batch_id


def batch_cost(results: list[BatchResult], prices: PriceTable, model: str) -> float:
    """Estimated cost of the succeeded results, with the configured batch discount applied."""
    total = Usage()
    for r in results:
        if r.message is not None:
            total = total + r.message.usage
    return prices.cost(model, total, batch=True)
