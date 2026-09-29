"""Emit object-store lifecycle rules from a retention policy (provisional, 0.95, #18 step 5).

CAMBER's retention policy (``camber retention``; the policy object belongs to
:mod:`camber.portfolio`) decides how long each data class is kept. When the landing lives in a
cloud bucket, the bucket's own lifecycle rules are the backstop that expires what the policy no
longer keeps. This module turns a policy dict into the lifecycle JSON of each provider --
**it only emits text; it never calls a cloud API.** The admin reviews the rules and applies them
with the provider's tool (see docs/EDGE-DEPLOY.md).

**Input schema** (a plain dict)::

    {
      "defaults":    {"<data class>": <rule>, ...},        # e.g. camber.portfolio.DEFAULT_POLICY
      "overrides":   {"<facility_id>": {"<data class>": <rule>}},   # optional
      "legal_holds": {"<facility_id>": {...}},                       # optional
    }

A bare defaults dict (data classes at the top level), the ``_portfolio.json`` shape
(``{"retention": {"defaults", "overrides"}, "legal_holds"}``) and the policy document of
``Portfolio.retention_policy()`` / ``camber retention show --json`` (``"schema":
"camber.retention/1"``, see ``camber.portfolio.RETENTION_SCHEMA``) are accepted too.

A ``<rule>`` is one of ``{"keep_days": n}``, ``{"keep_months": n}``, ``{"keep_years": n}`` or
``{"keep": "indefinite" | "forever" | "equipment_life" | "legal_hold"}`` (no expiry); other keys
(``keep_versions``, ``keep_last``) are not expressible as an object age and are ignored here.

**Bucket data classes** and where they live under the landing prefix. The prefixes come from the
workspace layout -- each class's ``location`` pattern in the retention policy document
(``store/facility_id={facility_id}/...``, ``rollups/hourly/facility_id={facility_id}/...``,
``rollups/daily/facility_id={facility_id}/...``) -- in one of two ``layout`` s:

* ``"store"`` (the default): the landing prefix is the **store root**, as the edge forwarder writes
  it. ``raw_trends`` at ``facility_id=<id>/``, ``hourly_rollups`` at
  ``rollups/hourly/facility_id=<id>/``, ``daily_rollups`` at ``rollups/daily/facility_id=<id>/``
  (the workspace's rollup directories, if mirrored to the bucket, sit under the store prefix).
* ``"workspace"``: the prefix mirrors the **whole workspace** (``aws s3 sync <workspace> ...``):
  ``store/facility_id=<id>/``, ``rollups/hourly/...``, ``rollups/daily/...`` verbatim.

``class_prefixes`` overrides them. A policy document's own ``location`` patterns are used when
given one; otherwise :data:`BUCKET_CLASSES` (the same derivation from ``camber.portfolio``'s
classes, pinned by a test). The prefix covers both the ``year=/month=`` keys and the legacy
year-only keys under it. The other classes (findings, baselines, reports, the audit log) are not
bucket objects and get no rule; neither does the ``_quarantine/`` prefix (kept until released or
discarded).

**Ages are conservative:** a month counts as 31 days and a year as 366, so a bucket rule never
expires an object before the policy would. Object age is counted from the upload, not from the
data's timestamps, so a backfilled old month is kept longer, never shorter.

**Overrides and legal holds** cannot be carved out of one bucket-wide rule (the providers apply
every matching rule), so with any override or hold the rules are emitted per facility, and a
facility under a legal hold gets **no** rule -- set the provider's own legal hold / retention lock
on its prefix as well. Without a facility list, overrides or holds are refused.
"""

from __future__ import annotations

import copy
import re

__all__ = [
    "PROVIDERS",
    "BUCKET_CLASSES",
    "DAYS_PER_MONTH",
    "DAYS_PER_YEAR",
    "rule_days",
    "LAYOUTS",
    "class_prefixes_from_policy",
    "normalize_policy",
    "policy_from_portfolio",
    "bucket_lifecycle_rules",
]

PROVIDERS = ("s3", "gcs", "azure")
# Store-layout prefixes per bucket class: camber.portfolio's CLASSES[...]["location"] up to the
# facility_id= segment, with the store directory stripped (tests pin the equality).
BUCKET_CLASSES: dict = {
    "raw_trends": "",
    "hourly_rollups": "rollups/hourly/",
    "daily_rollups": "rollups/daily/",
}
LAYOUTS = ("store", "workspace")
_STORE_DIR = "store/"  # the workspace's store directory in the policy's location patterns
_FID_SEGMENT = "facility_id={facility_id}"
POLICY_DOC_SCHEMA = "camber.retention/1"
DAYS_PER_MONTH = 31
DAYS_PER_YEAR = 366
_NO_EXPIRY = ("indefinite", "forever", "equipment_life", "legal_hold")
_MAX_RULES = {"s3": 1000, "gcs": 100, "azure": 100}
_AZURE_PREFIXES_PER_RULE = 10
_APPLY_HINT = {
    "s3": "aws s3api put-bucket-lifecycle-configuration --bucket <bucket> "
    "--lifecycle-configuration file://<rules.json>",
    "gcs": "gcloud storage buckets update gs://<bucket> --lifecycle-file=<rules.json>",
    "azure": "az storage account management-policy create --account-name <account> "
    "--resource-group <group> --policy @<rules.json>",
}


def rule_days(rule) -> int | None:
    """Expiry age in days for one retention rule, or ``None`` when it never expires."""
    if not isinstance(rule, dict):
        raise ValueError(f"a retention rule must be a dict, got {rule!r}")
    for key, mult in (
        ("keep_days", 1),
        ("keep_months", DAYS_PER_MONTH),
        ("keep_years", DAYS_PER_YEAR),
    ):
        if key in rule:
            n = rule[key]
            if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
                raise ValueError(f"{key} must be a positive whole number, got {n!r}")
            return n * mult
    keep = rule.get("keep")
    if keep in _NO_EXPIRY:
        return None
    raise ValueError(
        f"unrecognised retention rule {rule!r}: use keep_days / keep_months / keep_years, or "
        f"keep: {' | '.join(_NO_EXPIRY)}"
    )


def _is_document(policy) -> bool:
    return isinstance(policy, dict) and policy.get("schema") == POLICY_DOC_SCHEMA


def class_prefixes_from_policy(policy=None, *, layout: str = "store") -> dict:
    """``{bucket class: key prefix}`` from a policy document's ``classes[].location`` patterns.

    ``policy`` is a ``camber.retention/1`` document (``Portfolio.retention_policy()``); without
    one (or for a class it does not locate) the pinned :data:`BUCKET_CLASSES` are used. In the
    ``"store"`` layout the store directory (the head of ``raw_trends``' location) is stripped; in
    ``"workspace"`` the heads are kept verbatim.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r} (known: {', '.join(LAYOUTS)})")
    heads = {c: (_STORE_DIR if c == "raw_trends" else p) for c, p in BUCKET_CLASSES.items()}
    classes = (policy or {}).get("classes") if _is_document(policy) else None
    for cls in BUCKET_CLASSES:
        loc = ((classes or {}).get(cls) or {}).get("location")
        if isinstance(loc, str) and _FID_SEGMENT in loc:
            heads[cls] = loc.split(_FID_SEGMENT, 1)[0]
    if layout == "workspace":
        return heads
    store = heads["raw_trends"]
    return {c: (h[len(store) :] if store and h.startswith(store) else h) for c, h in heads.items()}


def normalize_policy(policy: dict) -> dict:
    """``{"defaults", "overrides", "legal_holds"}`` from any accepted policy shape (a copy)."""
    if not isinstance(policy, dict):
        raise ValueError("the retention policy must be a JSON object")
    p = copy.deepcopy(policy)
    if _is_document(p):  # Portfolio.retention_policy() / camber retention show --json
        strip = ("source", "min_age_days")
        overrides: dict = {}
        for fid, fac in (p.get("facilities") or {}).items():
            own = {
                c: {k: v for k, v in r.items() if k not in strip}
                for c, r in ((fac or {}).get("rules") or {}).items()
                if isinstance(r, dict) and r.get("source") == "facility"
            }
            if own:
                overrides[fid] = own
        return {
            "defaults": {
                c: {k: v for k, v in r.items() if k not in strip}
                for c, r in (p.get("defaults") or {}).items()
            },
            "overrides": overrides,
            "legal_holds": {fid: {} for fid in p.get("legal_holds") or ()},
        }
    if "retention" in p and isinstance(p["retention"], dict):  # the _portfolio.json shape
        ret = p["retention"]
        return {
            "defaults": dict(ret.get("defaults") or {}),
            "overrides": dict(ret.get("overrides") or {}),
            "legal_holds": dict(p.get("legal_holds") or {}),
        }
    if "defaults" in p:
        return {
            "defaults": dict(p.get("defaults") or {}),
            "overrides": dict(p.get("overrides") or {}),
            "legal_holds": dict(p.get("legal_holds") or {}),
        }
    return {"defaults": p, "overrides": {}, "legal_holds": {}}


def policy_from_portfolio(portfolio) -> tuple:
    """``(policy document, facility ids)`` read from a workspace through the portfolio API.

    The document is ``Portfolio.retention_policy()`` (``"schema": "camber.retention/1"``, see
    ``camber.portfolio.RETENTION_SCHEMA``): the defaults, each facility's effective rules with
    their source, the legal holds and each class's ``location``. The precedence (legal hold >
    facility override > default) is therefore the portfolio's own, and
    :func:`bucket_lifecycle_rules` takes the class prefixes from the locations.
    """
    doc = portfolio.retention_policy()
    return doc, sorted(doc.get("facilities") or {})


def _join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def _azure_name(cls: str, days: int, i: int) -> str:
    return "camber" + re.sub(r"[^A-Za-z0-9]", "", cls.title()) + f"{days}d{i}"


def bucket_lifecycle_rules(
    policy: dict,
    *,
    provider: str,
    facilities=None,
    prefix: str = "",
    container=None,
    class_prefixes=None,
    layout: str = "store",
) -> dict:
    """The provider's lifecycle document for ``policy``, plus a plan and notes (text only).

    ``provider`` is ``"s3"``, ``"gcs"`` or ``"azure"`` (``container`` is then required: Azure
    prefix filters start with the container name). ``prefix`` is the landing's key prefix (the
    sink's ``prefix``). ``facilities`` lists the facility ids to emit per-facility rules for;
    without it one bucket-wide rule per data class is emitted, which is refused when the policy
    has overrides or legal holds. ``layout`` (``"store"`` / ``"workspace"``) says what
    ``prefix`` is the root of; the class prefixes come from a policy document's locations when
    ``policy`` is one (see :func:`class_prefixes_from_policy`), and ``class_prefixes`` overrides
    them. Returns ``{"provider", "document", "plan", "notes", "apply_with"}``; ``document`` is
    the JSON to hand to the provider's tool.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r} (known: {', '.join(PROVIDERS)})")
    if provider == "azure" and not container:
        raise ValueError("Azure rules need the container name (prefix filters start with it)")
    pol = normalize_policy(policy)
    classes = class_prefixes_from_policy(policy, layout=layout)
    classes.update(class_prefixes or {})
    notes = []
    for cls in sorted(set(pol["defaults"]) - set(classes)):
        notes.append(f"{cls}: not stored in the landing bucket; no rule")
    notes.append("_quarantine/: no rule (kept until released or discarded)")
    holds, overrides = pol["legal_holds"], pol["overrides"]
    if facilities is None and (holds or overrides):
        raise ValueError(
            "the policy has facility overrides or legal holds, which a bucket-wide rule would "
            "ignore: pass the facility list (or --workspace) to emit per-facility rules"
        )
    root = f"{container}/" if provider == "azure" else ""
    # plan rows: (class, days, [prefixes])
    plan = []
    for cls, cls_prefix in classes.items():
        base = pol["defaults"].get(cls)
        if base is None:
            notes.append(f"{cls}: not in the policy; no rule")
            continue
        if facilities is None:
            days = rule_days(base)
            if days is None:
                notes.append(f"{cls}: kept {base.get('keep')}; no rule")
                continue
            plan.append((cls, days, [root + _join(prefix, cls_prefix, "facility_id=")]))
            continue
        groups: dict = {}
        for fid in sorted(facilities):
            if fid in holds:
                notes.append(
                    f"{cls} {fid}: legal hold; no rule (set the provider's own legal hold too)"
                )
                continue
            rule = (overrides.get(fid) or {}).get(cls, base)
            days = rule_days(rule)
            if days is None:
                continue
            groups.setdefault(days, []).append(
                root + _join(prefix, cls_prefix, f"facility_id={fid}") + "/"
            )
        for days, prefixes in sorted(groups.items()):
            plan.append((cls, days, prefixes))
        if not groups:
            notes.append(f"{cls}: no facility's data expires; no rule")

    rules: list
    if provider == "s3":
        rules = [
            {
                "ID": f"camber-{cls}-{days}d-{i}",
                "Status": "Enabled",
                "Filter": {"Prefix": p},
                "Expiration": {"Days": days},
            }
            for cls, days, prefixes in plan
            for i, p in enumerate(prefixes)
        ]
        document: dict = {"Rules": rules}
    elif provider == "gcs":
        rules = [
            {"action": {"type": "Delete"}, "condition": {"age": days, "matchesPrefix": prefixes}}
            for cls, days, prefixes in plan
        ]
        document = {"rule": rules}
    else:
        rules = []
        for cls, days, prefixes in plan:
            for i in range(0, len(prefixes), _AZURE_PREFIXES_PER_RULE):
                rules.append(
                    {
                        "enabled": True,
                        "name": _azure_name(cls, days, i // _AZURE_PREFIXES_PER_RULE),
                        "type": "Lifecycle",
                        "definition": {
                            "actions": {
                                "baseBlob": {"delete": {"daysAfterModificationGreaterThan": days}}
                            },
                            "filters": {
                                "blobTypes": ["blockBlob"],
                                "prefixMatch": prefixes[i : i + _AZURE_PREFIXES_PER_RULE],
                            },
                        },
                    }
                )
        document = {"rules": rules}
    if len(rules) > _MAX_RULES[provider]:
        raise ValueError(
            f"{len(rules)} rules exceed the {provider} limit of {_MAX_RULES[provider]}: group "
            "facilities under fewer overrides, or apply the default bucket-wide"
        )
    notes.append(
        "applying REPLACES the bucket's whole lifecycle configuration: merge these rules with any "
        "existing ones first"
    )
    return {
        "provider": provider,
        "document": document,
        "plan": [{"class": c, "days": d, "prefixes": p} for c, d, p in plan],
        "notes": notes,
        "apply_with": _APPLY_HINT[provider],
    }
