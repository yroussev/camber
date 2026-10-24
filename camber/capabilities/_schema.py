"""A small JSON Schema 2020-12 validator: the subset the capability schemas use (0.110, #275).

Supported keywords: ``type`` (one name or a list), ``properties``, ``required``,
``additionalProperties`` (a boolean or a schema), ``enum``, ``const``, ``items`` (one schema for
every element), ``minItems`` / ``maxItems``, ``minimum`` / ``maximum``, ``exclusiveMinimum`` /
``exclusiveMaximum``, ``minLength`` / ``maxLength`` and ``pattern`` (``re.search``, as the spec
says: anchor it to match the whole string). Annotations (``$schema``, ``$id``, ``$comment``,
``title``, ``description``, ``default``, ``examples``, ``format``) are accepted and ignored;
``default`` is **not** applied -- a handler reads a missing key with its own default.

:func:`check_schema` refuses any other keyword, so a capability can never declare a constraint
(``oneOf``, ``$ref``, ``uniqueItems``, ...) that this validator would silently skip. Standard
library only. Private module -- not public API.
"""

from __future__ import annotations

import math
import re

_TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
_ANNOTATIONS = frozenset(
    {"$schema", "$id", "$comment", "title", "description", "default", "examples", "format"}
)
_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "const",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
    }
)


def check_schema(schema, where: str = "#") -> None:
    """Raise ``ValueError`` unless ``schema`` uses only the supported subset, well-formed."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValueError(f"{where}: a schema must be an object or a boolean")
    unknown = sorted(set(schema) - _KEYWORDS - _ANNOTATIONS)
    if unknown:
        raise ValueError(f"{where}: unsupported schema keyword(s) {', '.join(unknown)}")
    t = schema.get("type")
    if t is not None:
        names = t if isinstance(t, list) else [t]
        bad = [n for n in names if n not in _TYPES]
        if bad or not names:
            raise ValueError(f"{where}: unknown type {t!r}")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        raise ValueError(f"{where}: 'properties' must be an object")
    for k, sub in props.items():
        check_schema(sub, f"{where}/properties/{k}")
    req = schema.get("required", [])
    if not (isinstance(req, list) and all(isinstance(r, str) for r in req)):
        raise ValueError(f"{where}: 'required' must be a list of names")
    if "additionalProperties" in schema:
        check_schema(schema["additionalProperties"], f"{where}/additionalProperties")
    if "items" in schema:
        check_schema(schema["items"], f"{where}/items")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise ValueError(f"{where}: 'enum' must be a list")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (re.error, TypeError) as e:
            raise ValueError(f"{where}: bad 'pattern': {e}") from None


def _is_type(value, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, (list, tuple))
    if isinstance(value, bool):  # JSON has no 1 == true
        return False
    if name == "number":
        return isinstance(value, (int, float))
    # integer: any number with a zero fractional part (2020-12, section 6.1.1)
    return isinstance(value, int) or (
        isinstance(value, float) and math.isfinite(value) and value.is_integer()
    )


def _same(a, b) -> bool:
    """JSON equality: ``1 == 1.0`` but ``True != 1``."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def validate(instance, schema, path: str = "") -> list:
    """The errors of ``instance`` against ``schema``: ``["<path>: <problem>", ...]`` (``[]`` when
    valid). A path is a JSON pointer (``/facility_id``, ``/points/3/role``; ``""`` the root)."""
    where = path or "/"
    if schema is True:
        return []
    if schema is False:
        return [f"{where}: no value is allowed here"]
    errors: list = []
    t = schema.get("type")
    if t is not None:
        names = t if isinstance(t, list) else [t]
        if not any(_is_type(instance, n) for n in names):
            return [f"{where}: expected {' or '.join(names)}, got {_json_type(instance)}"]
    if "enum" in schema and not any(_same(instance, v) for v in schema["enum"]):
        errors.append(f"{where}: must be one of {', '.join(repr(v) for v in schema['enum'])}")
    if "const" in schema and not _same(instance, schema["const"]):
        errors.append(f"{where}: must be {schema['const']!r}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']} character(s)")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{where}: longer than {schema['maxLength']} character(s)")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{where}: does not match the pattern {schema['pattern']!r}")
    if _is_type(instance, "number"):
        for key, ok, word in (
            ("minimum", lambda v, b: v >= b, "below the minimum"),
            ("maximum", lambda v, b: v <= b, "above the maximum"),
            ("exclusiveMinimum", lambda v, b: v > b, "not above"),
            ("exclusiveMaximum", lambda v, b: v < b, "not below"),
        ):
            if key in schema and not ok(instance, schema[key]):
                errors.append(f"{where}: {instance!r} is {word} {schema[key]!r}")
    if isinstance(instance, (list, tuple)):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{where}: fewer than {schema['minItems']} item(s)")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{where}: more than {schema['maxItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(instance):
                errors += validate(item, schema["items"], f"{path}/{i}")
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{where}: missing required property {name!r}")
        extra = schema.get("additionalProperties", True)
        for k in sorted(instance, key=str):
            sub = f"{path}/{k}"
            if k in props:
                errors += validate(instance[k], props[k], sub)
            elif extra is False:
                errors.append(f"{where}: unknown property {k!r}")
            elif isinstance(extra, dict):
                errors += validate(instance[k], extra, sub)
    return errors


def _json_type(value) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return type(value).__name__
