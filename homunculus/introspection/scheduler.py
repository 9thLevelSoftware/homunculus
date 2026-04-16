"""Scheduler for determining and executing which introspection modes run each cycle."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import HomunculusConfig
    from ..models import IntrospectionResult
    from ..orchestrator.teacher import OpenAICompatibleTeacher
    from ..storage import ArtifactStore

logger = logging.getLogger(__name__)


@dataclass
class ScheduledModes:
    """Flags indicating which introspection modes should run this cycle."""

    metrics: bool = False
    critique: bool = False
    coverage: bool = False
    comparative: bool = False

    def scheduled_names(self) -> list[str]:
        """Return list of mode names that are scheduled to run."""
        names = []
        if self.metrics:
            names.append("metrics")
        if self.critique:
            names.append("critique")
        if self.coverage:
            names.append("coverage")
        if self.comparative:
            names.append("comparative")
        return names

    def any_scheduled(self) -> bool:
        """Return True if any mode is scheduled to run."""
        return self.metrics or self.critique or self.coverage or self.comparative


class IntrospectionScheduler:
    """Determines which introspection modes run based on cycle number.

    Mode rotation is based on intervals configured in IntrospectionSettings.
    Cycle 0 is skipped to avoid modulo edge case (0 % n == 0 for all n).

    Default intervals:
    - metrics: every 1 cycle
    - critique: every 3 cycles
    - coverage: every 5 cycles
    - comparative: every 3 cycles
    """

    def __init__(
        self,
        config: "HomunculusConfig",
        store: "ArtifactStore | None" = None,
        teacher: "OpenAICompatibleTeacher | None" = None,
    ) -> None:
        """Initialize the scheduler.

        Args:
            config: Homunculus config (used for introspection settings).
            store: Optional artifact store. Required for run_due_modes() to
                execute modes (modes need a store in their context). When None,
                only get_scheduled_modes() (pure scheduling) is usable.
            teacher: Optional teacher client for CritiqueMode. When None,
                CritiqueMode will create its own teacher from config at run time.
        """
        self.config = config
        self.settings = config.introspection
        self.store = store
        self.teacher = teacher

    def get_scheduled_modes(self, cycle_number: int) -> ScheduledModes:
        """Determine which modes should run for the given cycle number.

        Args:
            cycle_number: Current daemon cycle (1-indexed, cycle 0 is skipped)

        Returns:
            ScheduledModes with flags for each mode
        """
        # Skip cycle 0 to avoid modulo edge case (0 % n == 0 for all n)
        if cycle_number == 0:
            return ScheduledModes()

        # If introspection is disabled globally, return all False
        if not self.settings.enabled:
            return ScheduledModes()

        modes = ScheduledModes()

        # Check each mode against its interval
        if cycle_number % self.settings.metrics_interval == 0:
            modes.metrics = True

        # Critique can be disabled independently to save API costs
        if self.settings.critique_enabled and cycle_number % self.settings.critique_interval == 0:
            modes.critique = True

        if cycle_number % self.settings.coverage_interval == 0:
            modes.coverage = True

        if cycle_number % self.settings.comparative_interval == 0:
            modes.comparative = True

        return modes

    def run_due_modes(self, cycle_number: int) -> list["IntrospectionResult"]:
        """Execute all introspection modes that are due for the given cycle.

        Determines which modes are scheduled via get_scheduled_modes(), then
        runs each one against an IntrospectionContext built from this scheduler's
        store and config. Failures in any single mode are logged and skipped —
        a broken mode does not prevent the others from running.

        This is the entry point the daemon uses each cycle. Persistence of the
        returned results is the caller's responsibility (the daemon writes them
        via store.append_introspection_result).

        Args:
            cycle_number: Current daemon cycle (1-indexed; cycle 0 is skipped
                by get_scheduled_modes to avoid the modulo-zero edge case).

        Returns:
            List of IntrospectionResult, one per mode that ran successfully.
            Empty list when introspection is disabled, no modes are due,
            or no store is configured.
        """
        if self.store is None:
            logger.debug(
                "IntrospectionScheduler.run_due_modes called without a store; "
                "skipping execution (modes need a store in their context)."
            )
            return []

        scheduled = self.get_scheduled_modes(cycle_number)
        if not scheduled.any_scheduled():
            return []

        # Lazy import to keep scheduler.py free of circular import risk.
        from . import get_introspection_mode
        from .base import IntrospectionContext

        context = IntrospectionContext(
            store=self.store,
            config=self.config,
            cycle_number=cycle_number,
            window_size=self.settings.window_size,
        )

        results: list[IntrospectionResult] = []
        for mode_name in scheduled.scheduled_names():
            try:
                mode = get_introspection_mode(mode_name)
                # Critique mode accepts an optional teacher via constructor.
                # The factory builds it without args; if we have a teacher,
                # rebuild with it so we don't pay for a second teacher client.
                if mode_name == "critique" and self.teacher is not None:
                    from .critique import CritiqueMode
                    mode = CritiqueMode(teacher=self.teacher)
                result = mode.run(context)
            except Exception as exc:
                logger.warning(
                    "Introspection mode %r failed during cycle %d: %s",
                    mode_name, cycle_number, exc,
                )
                continue
            results.append(result)

        return results
