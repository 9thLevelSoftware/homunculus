"""Phase 5 autonomy instrumentation.

Public surface: dataclasses from :mod:`homunculus.autonomy.models`,
plus :func:`generate_report`, :class:`Watchdog`,
:func:`run_preflight`, and :func:`validate_acceptance`.
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
from .preflight import run_preflight
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
    "run_preflight",
    "validate_acceptance",
]
