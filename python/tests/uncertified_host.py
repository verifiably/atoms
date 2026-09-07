"""The declaration that a host cannot supply the certified kernel and volume tuple.

Named for the stack, not for this project: beliefs needs the same switch, because
the exception it converts is raised from here. One variable means one thing to set
in CI and one thing to explain.
"""

import os

UNCERTIFIED_HOST_VAR = "VERIFIABLY_UNCERTIFIED_HOST"


def uncertified_host(environ: "os._Environ[str] | dict[str, str]") -> bool:
    """Whether this host has declared it cannot supply the certified tuple.

    Opt-in, and set in one place: the CI workflow. Anywhere it is unset — a
    developer machine, the pre-push gate, the certified host — the suite runs
    closed and `CapabilityUnavailable` is a failure. Only an explicit "1"
    counts, so an empty or stray value cannot quietly disarm the gate.
    """
    return environ.get(UNCERTIFIED_HOST_VAR, "") == "1"
