"""Bridge to **ace-bacnet-devices** (optional ``[bacnet-vendor]`` extra) — typed decoding + hints.

[ace-bacnet-devices](https://github.com/ACE-IoT-Solutions/ace-bacnet-devices) (MIT) ships vendor
*proprietary* BACnet property / object-type definitions extracted from PICS conformance statements.
CAMBER uses it two ways, both optional:

1. **Typed decoding.** :func:`install_vendor_decoders` registers a vendor's proprietary-property
   decoders into a bacpypes3 stack so ``read_property`` returns typed values instead of raw octets.
   **Timing matters:** registration only affects the bacpypes3 app the *deployment* builds — CAMBER
   core builds no app (the discovery/read client is injected). So call this **at client-construction
   time**, before any read. The discovery ``vendor_bridge`` hook is a best-effort convenience — it
   cannot retroactively re-decode values read through an app that was built without the decoders.

2. **Mapping hints.** :func:`vendor_hint_tokens` / :func:`vendor_aliases` read the vendor extension
   catalog (plain data — no bacpypes3 needed) and surface proprietary property names/descriptions as
   extra tokens/aliases for the role-mapping path (:mod:`camber.interop.bacnet`).

Fully optional and lazy: nothing here is imported by :mod:`camber.interop.bacnet`; the library is
imported only when a function needs it, and the catalog can be injected via ``catalog=`` so the
transforms are testable with no library installed.
"""

from __future__ import annotations


def _require():
    try:
        import ace_bacnet_devices as abd
    except Exception as e:  # noqa: BLE001 - any import failure means the extra isn't usable
        raise ImportError(
            "the vendor-decoding bridge needs the optional extra: "
            'pip install "camber-toolkit[bacnet-vendor]"'
        ) from e
    return abd


def available_vendors() -> tuple:
    """Vendor names bundled by ace-bacnet-devices; ``()`` if the library is absent (no raise)."""
    try:
        abd = _require()
    except ImportError:
        return ()
    return tuple(abd.available())


def install_vendor_decoders(
    definition=None, *, stack: str = "bacpypes3", strict: bool = True, required: bool = False
) -> list:
    """Register vendor proprietary-property typed decoders into the bacpypes3 stack.

    ``definition=None`` installs every bundled vendor (``install_all``); else pass a vendor name
    or a ``VendorExtensions`` object to ``install``. Returns the installed vendor names. When the
    library or the bacpypes3 stack is unavailable: return ``[]`` if ``required=False`` (graceful
    no-op), else raise ``ImportError``. Call this at client-construction time — see the module note.
    """
    try:
        abd = _require()
    except ImportError:
        if required:
            raise
        return []
    if definition is None:
        res = abd.install_all(stack=stack, strict=strict)
        return list(res) if res is not None else list(available_vendors())
    res = abd.install(definition, stack=stack, strict=strict)
    if res is not None:
        return list(res)
    return [str(getattr(definition, "vendor_name", definition))]


def _catalogs(vendors=None, *, catalog=None) -> list:
    """The VendorExtensions to read — the injected ``catalog`` if given, else from the lib."""
    if catalog is not None:
        return [catalog]
    abd = _require()
    if vendors is None:
        return list(abd.vendors.load_all())
    return [abd.vendors.load(n) for n in vendors]


def _iter_properties(catalog):
    """Yield each proprietary property across a catalog's object-extensions + object-types."""
    for ext in getattr(catalog, "object_extensions", ()) or ():
        yield from getattr(ext, "properties", ()) or ()
    for ot in getattr(catalog, "object_types", ()) or ():
        yield from getattr(ot, "properties", ()) or ()


def vendor_hint_tokens(vendors=None, *, catalog=None) -> dict:
    """``{proprietary_property_name -> description}`` across the requested vendors' catalogs.

    ``vendors`` selects bundled vendor names (all if ``None``); ``catalog`` injects a catalog
    object directly (for tests, or your own extraction) so no installed library is required. These
    feed the role-review path as richer, human-readable tokens.
    """
    out: dict = {}
    for cat in _catalogs(vendors, catalog=catalog):
        for p in _iter_properties(cat):
            name = getattr(p, "name", None)
            if name:
                out[str(name)] = str(getattr(p, "description", "") or "")
    return out


def vendor_aliases(vendors=None, *, catalog=None, min_confidence: float = 0.85) -> dict:
    """Conservative ``{proprietary_property_name -> Role slug}`` for props whose name maps cleanly.

    Each proprietary property is treated as a pseudo-object (object type inferred from its datatype
    primitive: boolean→binary, enumerated→multistate, else analog), bounding the candidate roles,
    and its name ranked with :class:`camber.mapping_assist.FeatureSuggester`. **Strict**: emit an
    alias only when the top match is a genuine ``ngram`` hit clearing a high ``min_confidence`` —
    never a weak edit-distance or short-initials coincidence (which would silently mis-map a point).
    Most proprietary properties (serial numbers, config flags) map to nothing and surface via
    :func:`vendor_hint_tokens` instead. Feed to ``roles_from_bacnet(..., vendor_aliases=...)``.
    """
    from ..mapping_assist import FeatureSuggester
    from .bacnet import _candidate_roles

    out: dict = {}
    for cat in _catalogs(vendors, catalog=catalog):
        for p in _iter_properties(cat):
            name = getattr(p, "name", None)
            if not name:
                continue
            prim = getattr(getattr(p, "datatype", None), "primitive", None)
            prim = str(getattr(prim, "name", prim) or "").lower()
            otype = (
                "binaryValue"
                if "bool" in prim
                else "multiStateValue"
                if "enum" in prim
                else "analogValue"
            )
            ranked = FeatureSuggester(vocab=_candidate_roles(otype, "")).suggest(str(name), k=1)
            if ranked and ranked[0].confidence >= min_confidence and ranked[0].basis == "ngram":
                out[str(name)] = ranked[0].role
    return out


__all__ = [
    "available_vendors",
    "install_vendor_decoders",
    "vendor_hint_tokens",
    "vendor_aliases",
]
