"""
A deliberately small JSON Schema validator.

Supports the subset the extraction schemas use: type (including unions),
properties, required, items, enum, additionalProperties=false, minimum/maximum,
and maxItems. A full validator is a dependency for features nobody here uses;
this one is ~60 lines and every rule is tested.
"""

from __future__ import annotations

from typing import Any

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    found: list[str] = []
    expected = schema.get("type")
    if expected:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(value, n) for n in names):
            return [f"{path}: expected {'|'.join(names)}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{path}: {value!r} not in {schema['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            found.append(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                found.append(f"{path}: missing required '{key}'")
        for key, item in value.items():
            if key in props:
                found += errors(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                found.append(f"{path}: unexpected property '{key}'")

    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            found.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                found += errors(item, schema["items"], f"{path}[{i}]")
    return found


def validate(value: Any, schema: dict) -> None:
    problems = errors(value, schema)
    if problems:
        raise SchemaError(problems)


class SchemaError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems[:5]) + (" ..." if len(problems) > 5 else ""))
