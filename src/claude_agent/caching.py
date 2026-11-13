"""Prompt-caching helpers.

Caching pays off when a long, stable prefix (instructions, tool definitions, earlier turns) is
sent repeatedly. Breakpoints are applied to a *copy* of the request each turn, so the stored
history never accumulates them (the API allows only a few per request).

Content shorter than the model's minimum cacheable length is processed normally and simply
not cached, so adding a breakpoint is always safe. Check ``cache_read_input_tokens`` in the
usage to see whether it is working.
"""

from __future__ import annotations

import copy
from typing import Any

from claude_agent.errors import ConfigError


def _marker(ttl: str | None) -> dict[str, str]:
    if ttl is None:
        return {"type": "ephemeral"}
    if ttl not in {"5m", "1h"}:
        raise ConfigError("cache ttl must be '5m' or '1h'")
    return {"type": "ephemeral", "ttl": ttl}


def with_cache_control(
    request: dict[str, Any],
    *,
    system: bool = True,
    tools: bool = True,
    conversation: bool = True,
    ttl: str | None = None,
) -> dict[str, Any]:
    """Return a copy of ``request`` with cache breakpoints on the stable parts."""
    out = copy.deepcopy(request)
    marker = _marker(ttl)

    if system and out.get("system"):
        sys_value = out["system"]
        if isinstance(sys_value, str):
            out["system"] = [{"type": "text", "text": sys_value, "cache_control": marker}]
        elif isinstance(sys_value, list) and sys_value and isinstance(sys_value[-1], dict):
            sys_value[-1]["cache_control"] = marker

    if tools and out.get("tools"):
        out["tools"][-1]["cache_control"] = marker

    if conversation and out.get("messages"):
        last = out["messages"][-1]
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [{"type": "text", "text": content, "cache_control": marker}]
        elif isinstance(content, list) and content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = marker
    return out


def count_breakpoints(request: dict[str, Any]) -> int:
    """How many cache breakpoints a request carries (the API accepts at most four)."""
    n = 0
    sys_value = request.get("system")
    if isinstance(sys_value, list):
        n += sum(1 for b in sys_value if isinstance(b, dict) and "cache_control" in b)
    n += sum(1 for t in request.get("tools", []) if "cache_control" in t)
    for m in request.get("messages", []):
        if isinstance(m.get("content"), list):
            n += sum(1 for b in m["content"] if isinstance(b, dict) and "cache_control" in b)
    return n
