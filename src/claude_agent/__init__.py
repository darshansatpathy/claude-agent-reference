"""A small, dependency-free reference for building a production-grade agent on the Claude API."""

from __future__ import annotations

__version__ = "0.1.0"

from claude_agent.agent import Agent, AgentConfig, RunResult
from claude_agent.audit import AuditLog, Redactor
from claude_agent.batches import BatchClient
from claude_agent.budget import Budget
from claude_agent.client import ClientConfig, MessagesClient
from claude_agent.cost import ModelPrice, PriceTable
from claude_agent.errors import AgentError, ApiError, ConfigError, TransportError
from claude_agent.tools import Tool, ToolRegistry
from claude_agent.types import Message, Usage

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentError",
    "ApiError",
    "AuditLog",
    "BatchClient",
    "Budget",
    "ClientConfig",
    "ConfigError",
    "Message",
    "MessagesClient",
    "ModelPrice",
    "PriceTable",
    "Redactor",
    "RunResult",
    "Tool",
    "ToolRegistry",
    "TransportError",
    "Usage",
    "__version__",
]
