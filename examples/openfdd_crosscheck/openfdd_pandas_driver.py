"""Run open-fdd's pandas rule engine over exported frames (runs in open-fdd's own interpreter).

Launched by ``run_crosscheck.py`` as a subprocess with the python of a venv where open-fdd is
installed; it imports open-fdd's public API (``open_fdd.rules.run_rule``) and never imports CAMBER.
Input: a directory of ``<equip>.parquet`` frames whose columns are open-fdd role names (see
``role_map.json``) and a JSON file ``{"rules": [...], "params": {rule_id: {...}},
"poll_seconds": n, "equipment_type": "AHU"}``. Output: one JSON document with the engine version
and one ``RuleResult.to_dict()`` record per (equipment, rule), with ``equip`` added and the bulky
evidence block dropped.

    <openfdd-venv>/bin/python openfdd_pandas_driver.py FRAMES_DIR SPEC.json OUT.json
"""

from __future__ import annotations

import json
import os
import sys


def main(argv: list) -> int:
    """Entry point: ``FRAMES_DIR SPEC.json OUT.json``."""
    frames_dir, spec_path, out_path = argv
    import pandas as pd
    from open_fdd import __version__
    from open_fdd.rules import run_rule
    from open_fdd.version import manifest

    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    records = []
    for name in sorted(os.listdir(frames_dir)):
        if not name.endswith(".parquet"):
            continue
        equip = name[: -len(".parquet")]
        df = pd.read_parquet(os.path.join(frames_dir, name))
        df.attrs["equipment_id"] = equip
        df.attrs["equipment_type"] = spec.get("equipment_type", "AHU")
        for rule_id in spec["rules"]:
            res = run_rule(
                rule_id,
                df,
                params=spec.get("params", {}).get(rule_id) or None,
                poll_seconds=float(spec["poll_seconds"]),
            )
            d = res.to_dict()
            d.pop("evidence", None)
            metrics = d.get("metrics") or {}
            d["metrics"] = {
                k: metrics.get(k)
                for k in ("active_sample_count", "total_sample_count", "gate_source")
                if k in metrics
            }
            d["equip"] = equip
            records.append(d)
    doc = {
        "engine": {"name": "open-fdd pandas", "version": __version__, "manifest": manifest()},
        "poll_seconds": float(spec["poll_seconds"]),
        "records": records,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
