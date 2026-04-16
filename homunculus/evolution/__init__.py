"""Weight evolution system - LoRA merging, lineage tracking, validation."""

from .lineage import LineageTracker
from .merge import MergeManager, MergeResult, detect_backend

__all__ = [
    "LineageTracker",
    "MergeManager",
    "MergeResult",
    "detect_backend",
]
