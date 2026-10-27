"""A small JSON Schema 2020-12 validator: the subset the capability schemas use (0.110, #275).

Supported keywords: ``type`` (one name or a list), ``properties``, ``required``,
``additionalProperties`` (a boolean or a schema), ``enum``, ``const``, ``items`` (one schema for
every element), ``minItems`` / ``maxItems``, ``minimum`` / ``maximum``, ``exclusiveMinimum`` /
``exclusiveMaximum``, ``minLength`` / ``maxLength`` and ``pattern`` (searched, as the spec says:
anchor it to match the whole string; ``$`` is the end of the string, as in ECMA-262, not before
a final newline as in Python -- 0.111, #291). Annotations (``$schema``, ``$id``, ``$comment``,
``title``, ``description``, ``default``, ``examples``, ``format``) are accepted and ignored;
``default`` is **not** applied -- a handler reads a missing key with its own default.

:func:`check_schema` refuses any other keyword, so a capability can never declare a constraint
(``oneOf``, ``$ref``, ``uniqueItems``, ...) that this validator would silently skip. Standard
library only. Private module -- not public API.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

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
        errors.append(_not_in_enum(where, instance, schema))
    if "const" in schema and not _same(instance, schema["const"]):
        errors.append(f"{where}: must be {schema['const']!r}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']} character(s)")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{where}: longer than {schema['maxLength']} character(s)")
        if "pattern" in schema and not _pattern(schema["pattern"]).search(instance):
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


def as_integers(instance, schema):
    """``instance`` (already valid against ``schema``) with every float where the schema allows
    an integer but not a number as that ``int``: ``5.0`` passes an ``integer`` schema (2020-12)
    but is no slice index or ``range`` bound (0.111, #308). Returns a new container where
    anything changed; the input is not modified."""
    if not isinstance(schema, dict):
        return instance
    t = schema.get("type")
    names = t if isinstance(t, list) else [t]
    if isinstance(instance, float) and "integer" in names and "number" not in names:
        return int(instance)
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        return {k: as_integers(v, props.get(k, extra)) for k, v in instance.items()}
    if isinstance(instance, (list, tuple)) and "items" in schema:
        return [as_integers(v, schema["items"]) for v in instance]
    return instance


#: an enum this long is not listed in an error, only counted (0.111, #304: a 3 KB message)
_LIST_ENUM = 12


def _not_in_enum(where: str, instance, schema: dict) -> str:
    """The error for a value outside ``enum``: the values when there are few, else their count,
    the closest few and the schema's ``description`` (where the values are listed)."""
    import difflib

    values = schema["enum"]
    if len(values) <= _LIST_ENUM:
        return f"{where}: must be one of {', '.join(repr(v) for v in values)}"
    msg = f"{where}: {str(instance)[:100]!r} is not one of the {len(values)} allowed values"
    close = difflib.get_close_matches(str(instance), [str(v) for v in values], n=3)
    if close:
        msg += f" (closest: {', '.join(close)})"
    if schema.get("description"):
        msg += f"; {schema['description']}"
    return msg


def _json_type(value) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return type(value).__name__


@lru_cache(maxsize=256)
def _pattern(pattern: str) -> re.Pattern:
    """``pattern`` compiled with ECMA-262's ``$``: each unescaped ``$`` outside a character class
    becomes ``\\Z``, so ``^a$`` refuses ``"a\\n"`` (Python's ``$`` also matches before a final
    newline)."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "$":
            ch = "\\Z"
        out.append(ch)
        i += 1
    return re.compile("".join(out))
