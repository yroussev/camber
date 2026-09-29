"""0.92 (#15): the condenser-water tower-bypass valve leak rule, the two new roles and their Brick
classes, on synthetic plants."""

import numpy as np
import pandas as pd

from camber import faultlab
from camber.eval import benchmark
from camber.interop.brick import brick_mapping_report, roles_from_triples
from camber.model.roles import Role
from camber.rules.builtin import builtin_registry
from camber.rules.condenser_bypass_rule import CondenserBypassLeak


def _plant(*, frac=0.0, offset=0.0, days=10, valve=0.0, status=True, seed=0):
    idx = pd.date_range("2026-07-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    load = 0.3 + 0.7 * rng.random(len(idx))
    tower = 78.0 + 4.0 * load + rng.normal(0, 0.2, len(idx))
    ret = tower + 10.0 * load
    entering = (1 - frac) * tower + frac * ret + rng.normal(0, 0.1, len(idx))
    f = pd.DataFrame(
        {
            Role.CW_BYPASS_VALVE: np.full(len(idx), valve),
            Role.CW_SUPPLY_TEMP: tower + offset,  # a biased tower-leaving sensor
            Role.CW_RETURN_TEMP: ret,
            Role.COND_ENTERING_WATER_TEMP: entering,
        },
        index=idx,
    )
    if status:
        f[Role.COMPRESSOR_STATUS] = 1.0
    return f


def test_leak_severity_scales_and_estimates_the_fraction():
    r = CondenserBypassLeak()
    assert r.analyze("P", _plant()).severity == "ok"
    warn = r.analyze("P", _plant(frac=0.35))
    assert warn.severity == "warn" and warn.metrics["attribution"] == "valve"
    assert abs(warn.metrics["bypass_fraction_est"] - 0.35) < 0.05
    fault = r.analyze("P", _plant(frac=0.8))
    assert fault.severity == "fault" and "leaking" in fault.summary
    assert r.violation_mask(_plant(frac=0.8)).mean() > 0.5


def test_a_sensor_offset_is_not_a_leak():
    r = CondenserBypassLeak()
    low_tower = r.analyze("P", _plant(offset=-3.0))  # tower sensor reads 3F low
    assert low_tower.severity == "info" and low_tower.metrics["attribution"] == "sensor_offset"
    assert "not a bypass leak" in low_tower.summary
    high_tower = r.analyze("P", _plant(offset=3.0))  # entering reads colder: mixing can't do that
    assert high_tower.severity == "info" and high_tower.metrics["attribution"] == "sensor_offset"


def test_without_the_return_temperature_it_says_it_cannot_tell():
    f = _plant(offset=-3.0).drop(columns=[Role.CW_RETURN_TEMP])
    x = CondenserBypassLeak().analyze("P", f)
    assert x.severity == "warn" and any("cannot be told" in c for c in x.caveats)


def test_only_shut_running_samples_are_judged():
    r = CondenserBypassLeak()
    open_valve = r.analyze("P", _plant(frac=0.8, valve=60.0))  # bypassing on purpose
    assert open_valve.severity == "info" and open_valve.metrics["n_shut"] == 0
    off = _plant(frac=0.8)
    off[Role.COMPRESSOR_STATUS] = 0.0
    assert r.analyze("P", off).severity == "info"
    ungated = r.analyze("P", _plant(frac=0.8, status=False))
    assert ungated.severity == "fault" and any(
        "no chiller run status" in c for c in ungated.caveats
    )
    # a 0-1 valve command is read as a fraction
    frac_cmd = _plant(frac=0.8)
    frac_cmd[Role.CW_BYPASS_VALVE] = 0.01
    assert r.analyze("P", frac_cmd).severity == "fault"


def test_registered_and_evidence():
    reg = builtin_registry()
    assert "condenser_bypass_leak" in reg.names()
    ev = CondenserBypassLeak().evidence("P", _plant(frac=0.8))
    assert Role.COND_ENTERING_WATER_TEMP in ev.roles and ev.mask.any()


def test_faultlab_scenario_scores_clean():
    # promoted from PENDING_SCENARIOS to the gated SCENARIOS at the 0.92 sign-off
    sc = {"condenser_bypass_leak": faultlab.SCENARIOS["condenser_bypass_leak"]}
    recs = faultlab.labeled_records(scenarios=sc)
    rep = benchmark(recs, faultlab.targets(sc))
    c = rep.per_detector["condenser_bypass_leak"]
    assert c.true_positive_rate == 1.0 and c.false_positive_rate == 0.0
    assert "condenser_bypass_leak" not in faultlab.PENDING_SCENARIOS


def test_brick_bypass_valve_and_condenser_temperatures():
    types = {
        "TWV": "Valve_Position_Command",
        "TWV_FB": "Valve_Position_Sensor",
        "BYP": "Bypass_Command",
        "CT_LWT": "Leaving_Water_Temperature_Sensor",
        "CT_EWT": "Entering_Water_Temperature_Sensor",
        "CH_ECWT": "Entering_Condenser_Water_Temperature_Sensor",
        "B_LWT": "Leaving_Water_Temperature_Sensor",
        "CHV": "Bypass_Command",
    }
    has = {
        "CWBV": ["TWV", "TWV_FB", "BYP"],
        "CT_1": ["CT_LWT", "CT_EWT"],
        "CH_1": ["CH_ECWT"],
        "B_1": ["B_LWT"],
        "CHWV": ["CHV"],
    }
    kinds = {"CWBV": "Condenser_Water_Bypass_Valve", "CT_1": "Cooling_Tower", "CH_1": "Chiller"}
    kinds.update({"B_1": "Boiler", "CHWV": "Chilled_Water_Valve"})
    roles = roles_from_triples({**types, **kinds}, has)
    assert roles["TWV_FB"] == Role.CW_BYPASS_VALVE  # the position feedback wins over commands
    assert "TWV" not in roles and "BYP" not in roles
    assert roles["CT_LWT"] == Role.CW_SUPPLY_TEMP and roles["CT_EWT"] == Role.CW_RETURN_TEMP
    assert roles["CH_ECWT"] == Role.COND_ENTERING_WATER_TEMP  # the tower has its own point
    assert "B_LWT" not in roles and "CHV" not in roles
    # without a tower-owned point the chiller's entering condenser water stays the CW supply
    alone = roles_from_triples(
        {"CH_ECWT": "Entering_Condenser_Water_Temperature_Sensor", "CH_1": "Chiller"},
        {"CH_1": ["CH_ECWT"]},
    )
    assert alone == {"CH_ECWT": Role.CW_SUPPLY_TEMP}
    ttl = (
        "@prefix brick: <https://brickschema.org/schema/Brick#> .\n"
        "@prefix bldg: <urn:x#> .\n"
        "bldg:CWBV a brick:Condenser_Water_Bypass_Valve ; brick:hasPoint bldg:BYP .\n"
        "bldg:BYP a brick:Bypass_Command .\n"
    )
    rep = brick_mapping_report(ttl, backend="minimal")
    assert rep.roles == {"BYP": Role.CW_BYPASS_VALVE}
