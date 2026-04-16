"""Base protocol and context for introspection modes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..config import HomunculusConfig
    from ..models import IntrospectionResult
    from ..storage import ArtifactStore


@runtime_checkable
class IntrospectionMode(Protocol):
    """Protocol that all introspection modes must implement."""

    @property
    def name(self) -> str:
        """Mode identifier (e.g., 'metrics', 'critique')."""
        ...

    def run(self, context: "IntrospectionContext") -> "IntrospectionResult":
        """Execute introspection and return findings."""
        ...


@dataclass
class IntrospectionContext:
    """Context passed to each introspection mode during execution."""

    store: "ArtifactStore"
    config: "HomunculusConfig"
    cycle_number: int
    window_size: int = 50
