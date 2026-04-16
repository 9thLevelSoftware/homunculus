"""Scheduler for determining which introspection modes run each cycle."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import HomunculusConfig


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

    def __init__(self, config: "HomunculusConfig") -> None:
        self.config = config
        self.settings = config.introspection

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


# =============================================================================
# Daemon Integration Point (for future implementation)
# =============================================================================
#
# In daemon.py run_once() or run_continuous(), after incrementing cycle count:
#
#     from .introspection.scheduler import IntrospectionScheduler
#     from .introspection import IntrospectionContext
#
#     scheduler = IntrospectionScheduler(self.config)
#     modes = scheduler.get_scheduled_modes(state.cycles_completed)
#
#     if modes.any_scheduled():
#         context = IntrospectionContext(
#             store=store,
#             config=self.config,
#             cycle_number=state.cycles_completed,
#             window_size=self.config.introspection.window_size,
#         )
#
#         for mode_name in modes.scheduled_names():
#             mode = get_introspection_mode(mode_name)  # Factory lookup
#             result = mode.run(context)
#             store.append_introspection_result(result)
#
# =============================================================================
