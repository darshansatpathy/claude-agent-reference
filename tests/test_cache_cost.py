from __future__ import annotations

from pathlib import Path

import pytest

from claude_agent.caching import count_breakpoints, with_cache_control
from claude_agent.cost import ModelPrice, PriceTable
from claude_agent.errors import ConfigError
from claude_agent.types import Usage

REQ = {
    "model": "m",
    "system": "You are helpful.",
    "tools": [{"name": "a"}, {"name": "b"}],
    "messages": [{"role": "user", "content": "hello"}],
}


def test_breakpoints_land_on_the_stable_parts():
    out = with_cache_control(REQ)
    assert out["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in out["tools"][0] and "cache_control" in out["tools"][1]
    assert out["messages"][-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert count_breakpoints(out) == 3


def test_original_request_is_not_mutated():
    snapshot = repr(REQ)
    with_cache_control(REQ)
    assert repr(REQ) == snapshot and count_breakpoints(REQ) == 0


def test_block_list_content_gets_marker_on_last_block():
    req = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            }
        ]
    }
    out = with_cache_control(req)
    blocks = out["messages"][0]["content"]
    assert "cache_control" not in blocks[0] and "cache_control" in blocks[1]


def test_system_given_as_blocks():
    out = with_cache_control({"system": [{"type": "text", "text": "x"}], "messages": []})
    assert out["system"][-1]["cache_control"]


def test_parts_can_be_switched_off_and_ttl_set():
    out = with_cache_control(REQ, system=False, tools=False, ttl="1h")
    assert count_breakpoints(out) == 1
    assert out["messages"][-1]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_bad_ttl_is_rejected():
    with pytest.raises(ConfigError):
        with_cache_control(REQ, ttl="1d")


def test_empty_request_is_fine():
    assert with_cache_control({"model": "m"}) == {"model": "m"}


def test_cost_arithmetic():
    price = ModelPrice(input_per_mtok=2.0, output_per_mtok=10.0)
    u = Usage(
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_creation_input_tokens=100_000,
        cache_read_input_tokens=1_000_000,
    )
    expected = 2.0 + 5.0 + 0.2 * 1.25 + 2.0 * 0.10
    assert price.cost(u) == pytest.approx(expected)
    assert price.cost(u, batch_discount=0.5) == pytest.approx(expected / 2)


def test_negative_prices_rejected():
    with pytest.raises(ConfigError):
        ModelPrice(-1, 1)


def test_price_table_unknown_model_fails_closed():
    with pytest.raises(ConfigError):
        PriceTable({}).cost("nope", Usage())


def test_example_price_table_loads_and_is_marked_as_placeholder():
    path = Path(__file__).resolve().parents[1] / "prices.example.toml"
    table = PriceTable.load(path)
    assert "claude-sonnet-5-5" in table.models
    assert "PLACEHOLDER" in path.read_text()


def test_price_table_validation(tmp_path):
    def load(text):
        f = tmp_path / "p.toml"
        f.write_text(text)
        return PriceTable.load(f)

    with pytest.raises(ConfigError):
        load("")
    with pytest.raises(ConfigError):
        load("[models.x]\ninput_per_mtok = 1\n")
    with pytest.raises(ConfigError):
        load("batch_discount = 1.5\n[models.x]\ninput_per_mtok = 1\noutput_per_mtok = 1\n")
    with pytest.raises(ConfigError):
        PriceTable.load(tmp_path / "missing.toml")
    with pytest.raises(ConfigError):
        load("not = [valid")
    t = load("batch_discount = 0.5\n[models.x]\ninput_per_mtok = 1\noutput_per_mtok = 2\n")
    assert t.batch_discount == 0.5
    assert t.cost("x", Usage(input_tokens=1_000_000), batch=True) == pytest.approx(0.5)
