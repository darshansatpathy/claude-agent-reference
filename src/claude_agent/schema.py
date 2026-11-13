"""A deliberately small JSON Schema validator for tool inputs.

The model produces tool arguments, so they are untrusted input and must be checked before a
handler runs. This covers the subset tool schemas actually use: ``type``, ``properties``,
``required``, ``additionalProperties``, ``enum``, ``minimum``/``maximum``, ``minLength``/
``maxLength``, ``pattern`` and ``items``. Unknown keywords are ignored, never trusted.
"""

from __future__ import annotations

import re
from typing import Any

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Return a list of human-readable problems. Empty means valid."""
    problems: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, str) and not _is_type(value, expected):
        return [f"{path}: expected {expected}, got {_name(value)}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        problems += _string_rules(value, schema, path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: must be <= {schema['maximum']}")
    if isinstance(value, dict):
        problems += _object_rules(value, schema, path)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += validate(item, schema["items"], f"{path}[{i}]")
    return problems


def _is_type(value: Any, name: str) -> bool:
    types = _TYPES.get(name)
    if types is None:
        return True  # unknown type names are not enforced
    if name in {"integer", "number"} and isinstance(value, bool):
        return False
    return isinstance(value, types)


def _name(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def _string_rules(value: str, schema: dict[str, Any], path: str) -> list[str]:
    out: list[str] = []
    if "minLength" in schema and len(value) < schema["minLength"]:
        out.append(f"{path}: shorter than {schema['minLength']}")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        out.append(f"{path}: longer than {schema['maxLength']}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and re.search(pattern, value) is None:
        out.append(f"{path}: does not match {pattern!r}")
    return out


def _object_rules(value: dict[str, Any], schema: dict[str, Any], path: str) -> list[str]:
    out: list[str] = []
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            out.append(f"{path}.{name}: is required")
    for name, item in value.items():
        if name in props and isinstance(props[name], dict):
            out += validate(item, props[name], f"{path}.{name}")
        elif schema.get("additionalProperties", True) is False:
            out.append(f"{path}.{name}: is not an allowed property")
    return out
