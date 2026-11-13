from __future__ import annotations

import pytest

from claude_agent.schema import validate
from claude_agent.types import Message, Usage

OBJ = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": r"^A-\d{4}$"},
        "n": {"type": "integer", "minimum": 1, "maximum": 10},
        "amount": {"type": "number"},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": 3}},
        "note": {"type": "string", "minLength": 2},
    },
    "required": ["id"],
    "additionalProperties": False,
}


def test_valid_input_has_no_problems():
    assert validate({"id": "A-1234", "n": 3, "amount": 1.5, "kind": "a", "tags": ["x"]}, OBJ) == []


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        ({}, "is required"),
        ({"id": "1234"}, "does not match"),
        ({"id": "A-1234", "n": 0}, ">= 1"),
        ({"id": "A-1234", "n": 11}, "<= 10"),
        ({"id": "A-1234", "n": "3"}, "expected integer"),
        ({"id": "A-1234", "n": True}, "expected integer"),
        ({"id": "A-1234", "amount": "x"}, "expected number"),
        ({"id": "A-1234", "kind": "z"}, "one of"),
        ({"id": "A-1234", "tags": ["toolong"]}, "longer than 3"),
        ({"id": "A-1234", "tags": [1]}, "expected string"),
        ({"id": "A-1234", "note": "x"}, "shorter than 2"),
        ({"id": "A-1234", "extra": 1}, "not an allowed property"),
        ("not an object", "expected object"),
        (None, "expected object"),
    ],
)
def test_invalid_inputs_are_reported(value, needle):
    problems = validate(value, OBJ)
    assert problems and any(needle in p for p in problems), problems


def test_unknown_type_names_and_keywords_are_not_enforced():
    assert validate(1, {"type": "weird"}) == []
    assert validate("x", {"format": "email"}) == []


def test_additional_properties_default_allows_extras():
    assert validate({"a": 1}, {"type": "object"}) == []


def test_usage_arithmetic_and_parsing():
    u = Usage.from_dict({"input_tokens": 3, "output_tokens": 4, "cache_read_input_tokens": 5})
    assert (u + u).total_tokens == 24
    assert Usage.from_dict(None) == Usage()
    assert Usage.from_dict({"input_tokens": None}).input_tokens == 0


def test_message_properties_keep_unknown_blocks():
    raw = {
        "id": "m",
        "model": "x",
        "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "...", "signature": "abc"},
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
            {"type": "tool_use", "id": "t1", "name": "n", "input": {"k": 1}},
            {"type": "tool_use", "id": "t2", "name": "n", "input": "bad"},
        ],
        "usage": {},
    }
    m = Message.from_dict(raw)
    assert m.text == "ab"
    assert [u.id for u in m.tool_uses] == ["t1", "t2"] and m.tool_uses[1].input == {}
    assert m.content[0]["signature"] == "abc"


def test_message_tolerates_missing_content():
    assert Message.from_dict({"id": "m"}).content == []
