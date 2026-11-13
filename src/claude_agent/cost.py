"""Cost estimation from usage. Prices are configuration, never constants in code.

Rates change and differ by contract. ``prices.example.toml`` holds placeholders that you must
replace from your own pricing page or agreement before trusting any dollar figure. If a cost
budget is set and the model has no price, the agent refuses to start rather than guess.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from claude_agent.errors import ConfigError
from claude_agent.types import Usage


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float
    cache_write_multiplier: float = 1.25
    cache_read_multiplier: float = 0.10

    def __post_init__(self) -> None:
        values = (
            self.input_per_mtok,
            self.output_per_mtok,
            self.cache_write_multiplier,
            self.cache_read_multiplier,
        )
        if any(v < 0 for v in values):
            raise ConfigError("prices and multipliers must not be negative")

    def cost(self, usage: Usage, *, batch_discount: float = 0.0) -> float:
        per_token_in = self.input_per_mtok / 1_000_000
        per_token_out = self.output_per_mtok / 1_000_000
        total = (
            usage.input_tokens * per_token_in
            + usage.output_tokens * per_token_out
            + usage.cache_creation_input_tokens * per_token_in * self.cache_write_multiplier
            + usage.cache_read_input_tokens * per_token_in * self.cache_read_multiplier
        )
        return total * (1.0 - batch_discount)


@dataclass(frozen=True)
class PriceTable:
    models: dict[str, ModelPrice]
    batch_discount: float = 0.0

    def price_for(self, model: str) -> ModelPrice:
        try:
            return self.models[model]
        except KeyError:
            raise ConfigError(f"no price configured for model {model!r}") from None

    def cost(self, model: str, usage: Usage, *, batch: bool = False) -> float:
        return self.price_for(model).cost(
            usage, batch_discount=self.batch_discount if batch else 0.0
        )

    @classmethod
    def load(cls, path: Path) -> PriceTable:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read price table {path}: {exc}") from exc
        raw_models = data.get("models")
        if not isinstance(raw_models, dict) or not raw_models:
            raise ConfigError("price table needs a [models.<model-id>] section")
        models: dict[str, ModelPrice] = {}
        for name, entry in raw_models.items():
            if (
                not isinstance(entry, dict)
                or "input_per_mtok" not in entry
                or "output_per_mtok" not in entry
            ):
                raise ConfigError(f"model {name!r} needs input_per_mtok and output_per_mtok")
            models[name] = ModelPrice(
                input_per_mtok=float(entry["input_per_mtok"]),
                output_per_mtok=float(entry["output_per_mtok"]),
                cache_write_multiplier=float(entry.get("cache_write_multiplier", 1.25)),
                cache_read_multiplier=float(entry.get("cache_read_multiplier", 0.10)),
            )
        discount = float(data.get("batch_discount", 0.0))
        if not 0.0 <= discount < 1.0:
            raise ConfigError("batch_discount must be in [0, 1)")
        return cls(models=models, batch_discount=discount)
