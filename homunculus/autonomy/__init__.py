"""Phase 5 autonomy instrumentation.

Public surface: dataclasses from :mod:`homunculus.autonomy.models`,
plus :func:`generate_report`, :class:`Watchdog`,
:func:`run_preflight`, :func:`run_precheck`, and
:func:`validate_acceptance`.
"""
from __future__ import annotations

from .acceptance import validate_acceptance
from .models import (
    AcceptanceVerdict,
    AutonomyReport,
    CriterionResult,
    GateResult,
    PreflightResult,
    WatchdogSnapshot,
)
from .precheck import ThroughputPrecheck, run_precheck
from .preflight import run_preflight
from .reporter import generate_report
from .watchdog import Watchdog

__all__ = [
    "AcceptanceVerdict",
    "AutonomyReport",
    "CriterionResult",
    "GateResult",
    "PreflightResult",
    "ThroughputPrecheck",
    "Watchdog",
    "WatchdogSnapshot",
    "generate_report",
    "run_precheck",
    "run_preflight",
    "validate_acceptance",
]
