"""Phase 5 autonomy instrumentation.

Public surface is intentionally narrow: the dataclasses from
:mod:`homunculus.autonomy.models`, plus the :func:`generate_report`
entry point and the :class:`Watchdog` class. Higher-level CLI wiring
(preflight, acceptance predicates) lands in 05-02; this module carries
only the data contracts + reporter + watchdog.
"""
from __future__ import annotations

from .models import (
    AcceptanceVerdict,
    AutonomyReport,
    CriterionResult,
    GateResult,
    PreflightResult,
    WatchdogSnapshot,
)
from .reporter import generate_report
from .watchdog import Watchdog

__all__ = [
    "AcceptanceVerdict",
    "AutonomyReport",
    "CriterionResult",
    "GateResult",
    "PreflightResult",
    "WatchdogSnapshot",
    "Watchdog",
    "generate_report",
]
