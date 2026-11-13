"""Audit log: redacted, append-only, hash-chained JSON lines.

By default the log records *what happened* (which tool, how many tokens, what outcome) and
fingerprints of the content, not the content itself. Turning content logging on is an
explicit choice, and the redactor runs either way.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SENSITIVE_KEYS = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|authorization)")


def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        flip = not flip
    return total % 10 == 0


class Redactor:
    """Replaces credentials and personal identifiers with ``[REDACTED:kind]``."""

    def __init__(self, extra: dict[str, str] | None = None) -> None:
        self._patterns = list(_PATTERNS)
        for kind, pattern in (extra or {}).items():
            self._patterns.append((kind, re.compile(pattern)))

    def text(self, value: str) -> str:
        for kind, pattern in self._patterns:
            value = pattern.sub(f"[REDACTED:{kind}]", value)
        return _CARD.sub(self._card, value)

    @staticmethod
    def _card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "[REDACTED:card]" if 13 <= len(digits) <= 19 and _luhn(digits) else match.group(0)

    def any(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.any(v) for v in value]
        if isinstance(value, dict):
            return {
                k: (
                    "[REDACTED:secret_field]"
                    if isinstance(k, str) and _SENSITIVE_KEYS.search(k) and isinstance(v, str)
                    else self.any(v)
                )
                for k, v in value.items()
            }
        return value


def fingerprint(value: Any) -> str:
    """Short stable hash of a value, so events can be correlated without storing the value."""
    raw = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


_GENESIS = "0" * 64


class AuditLog:
    """Writes one JSON object per line. Each line carries the hash of the previous one."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        redactor: Redactor | None = None,
        log_content: bool = False,
        run_id: str | None = None,
    ) -> None:
        path = Path(path) if path is not None else None
        self.path = path
        self.redactor = redactor or Redactor()
        self.log_content = log_content
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.events: list[dict[str, Any]] = []
        self._prev = _GENESIS
        self._seq = 0
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._resume(path)

    def _resume(self, path: Path) -> None:
        if not path.exists():
            return
        last = ""
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last:
            record = json.loads(last)
            self._prev = record["hash"]
            self._seq = int(record["seq"]) + 1

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            body = {
                "seq": self._seq,
                "ts": round(time.time(), 3),
                "run_id": self.run_id,
                "event": event,
                **self.redactor.any(fields),
                "prev": self._prev,
            }
            digest = hashlib.sha256(_canonical(body).encode()).hexdigest()
            record = {**body, "hash": digest}
            self._prev = digest
            self._seq += 1
            self.events.append(record)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, sort_keys=True) + "\n")
            return record

    def content(self, value: Any) -> dict[str, Any]:
        """Fields describing a piece of content: always a fingerprint, the content if enabled."""
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
        out: dict[str, Any] = {"sha256_16": fingerprint(value), "chars": len(text)}
        if self.log_content:
            out["content"] = value
        return out


def _canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def verify_chain(path: Path) -> tuple[bool, int | None]:
    """Check that no line was altered, removed or reordered. Returns (ok, first_bad_seq)."""
    prev = _GENESIS
    expected_seq = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                claimed = record.pop("hash")
            except (json.JSONDecodeError, KeyError):
                return False, expected_seq
            actual = hashlib.sha256(_canonical(record).encode()).hexdigest()
            if record.get("prev") != prev or claimed != actual or record.get("seq") != expected_seq:
                return False, expected_seq
            prev = claimed
            expected_seq += 1
    return True, None
