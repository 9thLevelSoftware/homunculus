"""Introspection infrastructure for daemon self-improvement."""
from __future__ import annotations

from .base import IntrospectionContext, IntrospectionMode
from .comparative import ComparativeMode
from .coverage import CoverageMode
from .critique import CritiqueMode
from .metrics import MetricsMode
from .scheduler import IntrospectionScheduler, ScheduledModes

# Mode registry for factory lookup
_MODE_REGISTRY: dict[str, type] = {
    "metrics": MetricsMode,
    "critique": CritiqueMode,
    "coverage": CoverageMode,
    "comparative": ComparativeMode,
}


def get_introspection_mode(name: str) -> IntrospectionMode:
    """Factory function to get an introspection mode by name.

    Args:
        name: Mode identifier ("metrics", "critique", "coverage", "comparative")

    Returns:
        Instance of the requested introspection mode

    Raises:
        ValueError: If mode name is not recognized
    """
    mode_class = _MODE_REGISTRY.get(name)
    if mode_class is None:
        valid_modes = ", ".join(sorted(_MODE_REGISTRY.keys()))
        raise ValueError(f"Unknown introspection mode: '{name}'. Valid modes: {valid_modes}")
    return mode_class()


__all__ = [
    "ComparativeMode",
    "CoverageMode",
    "CritiqueMode",
    "IntrospectionContext",
    "IntrospectionMode",
    "IntrospectionScheduler",
    "ScheduledModes",
    "MetricsMode",
    "get_introspection_mode",
]
