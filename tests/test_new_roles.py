"""Role-table consistency: every role has a Haystack hint, and the 0.5 packaged/DX/refrigerant
roles are fully wired (bounds + hint + status classification) so no interop surface silently regresses.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role, HAYSTACK_HINT, STATUS_ROLES  # noqa: E402
from camber.sensorhealth import PHYSICAL_BOUNDS  # noqa: E402

_NEW_ROLES = [
    Role.COMPRESSOR_STATUS, Role.COMPRESSOR_STAGE, Role.CONDENSER_FAN_STATUS, Role.HEAT_STAGE,
    Role.REVERSING_VALVE_CMD, Role.FILTER_DIFF_PRESS, Role.SUPPLY_AIR_HUMIDITY,
    Role.RETURN_AIR_HUMIDITY, Role.COND_APPROACH_TEMP, Role.EVAP_APPROACH_TEMP,
]


def test_every_role_has_a_haystack_hint():
    missing = [r.value for r in Role if r not in HAYSTACK_HINT]
    assert missing == [], f"roles missing a HAYSTACK_HINT: {missing}"


def test_new_roles_have_physical_bounds():
    missing = [r.value for r in _NEW_ROLES if r not in PHYSICAL_BOUNDS]
    assert missing == [], f"new roles missing PHYSICAL_BOUNDS: {missing}"


def test_new_role_bounds_are_ordered():
    for r in _NEW_ROLES:
        lo, hi = PHYSICAL_BOUNDS[r]
        assert lo < hi, f"{r.value} bounds not ordered: {(lo, hi)}"


def test_binary_status_roles_classified_as_status():
    for r in (Role.COMPRESSOR_STATUS, Role.CONDENSER_FAN_STATUS, Role.REVERSING_VALVE_CMD):
        assert r in STATUS_ROLES, f"{r.value} should be a STATUS_ROLE (loaded as a step series)"
    # numeric stages are NOT status text signals
    assert Role.COMPRESSOR_STAGE not in STATUS_ROLES
    assert Role.HEAT_STAGE not in STATUS_ROLES


def test_new_role_slugs_are_stable():
    assert Role.COMPRESSOR_STATUS.value == "compressor_status"
    assert Role.COND_APPROACH_TEMP.value == "cond_approach_temp"
    assert Role("filter_diff_press") is Role.FILTER_DIFF_PRESS
