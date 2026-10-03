"""The facility lifecycle state machine: pure, no I/O.

::

    provisioning -> active <-> suspended -> offboarding -> archived -> purged (tombstone)
                                  ^             |             |
                                  +--restore----+----restore--+

Each *action* moves a facility from one of a set of states to exactly one state. The table is
complete here (so it is tested as a whole); ``offboard``, ``archive``, ``restore`` and ``purge``
run the export-bundle cascade (see :mod:`camber.portfolio._cascade` and docs/PORTFOLIO.md). A
**legal hold** blocks every action that deletes data.
"""

from __future__ import annotations

STATES = ("provisioning", "active", "suspended", "offboarding", "archived", "purged")

# action -> (states it may start from, state it ends in)
TRANSITIONS: dict = {
    "activate": (("provisioning",), "active"),
    "suspend": (("active",), "suspended"),
    "resume": (("suspended",), "active"),
    "offboard": (("active", "suspended"), "offboarding"),
    "restore": (("offboarding", "archived"), "active"),
    "archive": (("offboarding",), "archived"),
    "purge": (("archived",), "purged"),
}

# Actions that delete data: archive drops the hot store data (the bundle is kept), purge drops
# everything but the tombstone and the audit record. A legal hold refuses both.
DELETING = frozenset({"archive", "purge"})

# Actions implemented end to end (all of them since 0.95: offboard/restore/archive/purge run the
# export-bundle cascade in camber.portfolio._cascade).
IMPLEMENTED = frozenset(TRANSITIONS)


class LifecycleError(ValueError):
    """A lifecycle action that is not allowed from the facility's current state (or is held)."""


def allowed_actions(state: str) -> list:
    """The actions allowed from ``state``, in table order (``[]`` for ``purged``)."""
    if state not in STATES:
        raise LifecycleError(f"unknown lifecycle state {state!r} (known: {', '.join(STATES)})")
    return [a for a, (src, _dst) in TRANSITIONS.items() if state in src]


def transition(state: str, action: str, *, legal_hold: bool = False) -> str:
    """The state ``action`` moves a facility in ``state`` to; raises :class:`LifecycleError`.

    Refused when the action is unknown, not allowed from ``state`` (the message lists the allowed
    ones), or deletes data while the facility is under a legal hold.
    """
    if action not in TRANSITIONS:
        raise LifecycleError(
            f"unknown lifecycle action {action!r} (known: {', '.join(TRANSITIONS)})"
        )
    allowed = allowed_actions(state)
    src, dst = TRANSITIONS[action]
    if state not in src:
        hint = ", ".join(allowed) if allowed else "none -- purged is final"
        raise LifecycleError(
            f"cannot {action} a facility that is {state} (allowed from {state}: {hint})"
        )
    if legal_hold and action in DELETING:
        raise LifecycleError(
            f"cannot {action}: the facility is under a legal hold, which blocks every "
            "transition that deletes data (release the hold first)"
        )
    return dst
