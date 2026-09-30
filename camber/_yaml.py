"""YAML run configs (0.98, #90): an optional, equivalent alternative to JSON.

JSON stays the dependency-free default. YAML exists so a calibration note can sit as a comment
beside the value it explains. Reading YAML needs PyYAML, the optional ``[yaml]`` extra, imported
lazily here (the same pattern as :meth:`camber.ingest.bacnet_client.BacnetClientConfig.from_file`);
writing YAML needs nothing, since :func:`dump_yaml` emits the JSON-shaped subset itself.

The loader is YAML 1.2-flavoured so that a YAML config means exactly what the equivalent JSON
means: only ``true`` / ``false`` are booleans (``no``, ``on``, ``NO`` stay strings: a country code
is not a boolean), dates stay strings (``2018-07-01`` is not a ``date``), ``07:00`` is not a
base-60 integer, ``012`` is not octal, and ``1e-06`` is a float.
"""

from __future__ import annotations

import json
import math
import os
import re

YAML_SUFFIXES = (".yaml", ".yml")

_INSTALL_HINT = (
    "reading a YAML config needs PyYAML: pip install 'camber-toolkit[yaml]' "
    "(or pip install pyyaml), or use a .json config"
)


def is_yaml_path(path) -> bool:
    """True when ``path`` names a YAML file (``.yaml`` / ``.yml``, any case)."""
    return os.fspath(path).lower().endswith(YAML_SUFFIXES)


class MissingYamlExtra(ImportError):
    """PyYAML (the ``[yaml]`` extra) is needed to read a YAML config and is not installed."""


def _yaml():
    try:
        import yaml  # optional: the [yaml] extra
    except ImportError as e:
        raise MissingYamlExtra(_INSTALL_HINT) from e
    return yaml


_LOADER = None


def _loader():
    """A SafeLoader whose implicit types match JSON's (see the module docstring)."""
    global _LOADER
    if _LOADER is not None:
        return _LOADER
    yaml = _yaml()
    drop = {
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:timestamp",
    }

    class _ConfigLoader(yaml.SafeLoader):
        pass

    _ConfigLoader.yaml_implicit_resolvers = {
        first: [(tag, rx) for tag, rx in resolvers if tag not in drop]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _ConfigLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )
    _ConfigLoader.add_implicit_resolver(
        "tag:yaml.org,2002:int",
        re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"),
        list("-+0123456789"),
    )
    _ConfigLoader.add_implicit_resolver(
        "tag:yaml.org,2002:float",
        re.compile(
            r"^(?:[-+]?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
            r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
        ),
        list("-+0123456789."),
    )
    _LOADER = _ConfigLoader
    return _LOADER


def loads_yaml(text: str):
    """Parse YAML ``text`` into plain Python data (``None`` for an empty document)."""
    yaml = _yaml()
    try:
        return yaml.load(text, Loader=_loader())  # noqa: S506 -- a SafeLoader subclass
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML: {e}") from e


def read_config_file(path) -> dict:
    """Read a run config: ``.yaml`` / ``.yml`` as YAML (needs the extra), anything else as JSON."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if is_yaml_path(path):
        data = loads_yaml(text)
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise ValueError(f"{os.fspath(path)}: a config must be a mapping at the top level")
        return data
    return json.loads(text)


# --------------------------------------------------------------------------- writing

_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return ".nan"
        if math.isinf(v):
            return ".inf" if v > 0 else "-.inf"
        s = repr(v)
        if "e" in s and "." not in s.split("e")[0]:  # 1e-06 -> 1.0e-06 (valid YAML 1.1 too)
            m, _, x = s.partition("e")
            s = f"{m}.0e{x}"
        return s
    s = str(v)
    if _PLAIN.fullmatch(s) and s.lower() not in _RESERVED:
        return s
    return json.dumps(s, ensure_ascii=False)


def _wrap_comment(text: str, indent: str, width: int = 96) -> list:
    out = []
    for para in str(text).splitlines() or [""]:
        words, line = para.split(), ""
        for w in words:
            if line and len(indent) + 2 + len(line) + 1 + len(w) > width:
                out.append(f"{indent}# {line}")
                line = w
            else:
                line = f"{line} {w}" if line else w
        out.append(f"{indent}# {line}".rstrip())
    return out


def _block(obj, indent: str, path: tuple, notes: dict) -> list:
    """Lines for a mapping or sequence value at ``indent``."""
    lines: list = []
    if isinstance(obj, dict):
        if not obj:
            return [f"{indent}{{}}"]
        comment = obj.get("_comment")
        if isinstance(comment, str):
            lines += _wrap_comment(comment, indent)
        for k, v in obj.items():
            if k == "_comment" and isinstance(v, str):
                continue
            note = notes.get(path + (k,))
            if note:
                lines += _wrap_comment(note, indent)
            key = _scalar(str(k))
            if isinstance(v, (dict, list, tuple)) and v:
                lines.append(f"{indent}{key}:")
                lines += _block(v, indent + "  ", path + (k,), notes)
            else:
                lines.append(f"{indent}{key}: {_inline(v)}")
        return lines
    if isinstance(obj, (list, tuple)):
        if not obj:
            return [f"{indent}[]"]
        for i, item in enumerate(obj):
            if isinstance(item, (dict, list, tuple)) and item:
                sub = _block(item, indent + "  ", path + (i,), notes)
                # the first non-comment line carries the "- " marker
                j = next(n for n, s in enumerate(sub) if not s.lstrip().startswith("#"))
                sub[j] = f"{indent}- {sub[j][len(indent) + 2 :]}"
                lines += sub
            else:
                lines.append(f"{indent}- {_inline(item)}")
        return lines
    return [f"{indent}{_inline(obj)}"]


def _inline(v) -> str:
    if isinstance(v, dict):
        return "{}"
    if isinstance(v, (list, tuple)):
        return "[]" if not v else "[" + ", ".join(_inline(x) for x in v) + "]"
    return _scalar(v)


def dump_yaml(obj, *, notes: dict | None = None, header: str | None = None) -> str:
    """Render JSON-shaped data (dicts, lists, strings, numbers, booleans, ``None``) as YAML.

    Needs no PyYAML. A mapping's ``"_comment"`` string becomes ``#`` comment lines above its
    keys, so a JSON template's notes survive as YAML comments. ``notes`` maps a key path (a
    tuple of keys and list indexes, e.g. ``("rules", 0, "params", "fan_heat_f")``) to a comment
    written above that key; ``header`` is a comment at the top of the document.
    """
    lines = _wrap_comment(header, "") if header else []
    if isinstance(obj, (dict, list, tuple)) and obj:
        lines += _block(obj, "", (), notes or {})
    else:
        lines.append(_inline(obj))
    return "\n".join(lines) + "\n"
