"""Task prioritizer for ranking generated tasks by weighted factors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..models import GeneratedTask

if TYPE_CHECKING:
    from ..models import IntrospectionResult


@dataclass
class PriorityWeights:
    """Configurable weights for priority calculation.

    The three factors are:
    - alignment: How well the task aligns with introspection insights
    - complexity: Preference for simpler tasks (shorter prompts)
    - freshness: Preference for newer tasks (time decay)

    All weights must sum to 1.0.
    """

    alignment: float = 0.5
    complexity: float = 0.3
    freshness: float = 0.2

    def __post_init__(self) -> None:
        total = self.alignment + self.complexity + self.freshness
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"Priority weights must sum to 1.0, got {total}")


class TaskPrioritizer:
    """Ranks tasks by weighted combination of alignment, complexity, and freshness.

    This prioritizer applies a multi-factor scoring system:
    1. Alignment: Tasks from introspection score highest (1.0), user tasks use
       their existing priority, others get baseline (0.5)
    2. Complexity: Shorter prompts score higher (simpler tasks preferred)
    3. Freshness: Newer tasks score higher with time decay

    Tasks are deduplicated by prompt prefix before scoring. When priorities
    tie, older tasks (FIFO) are preferred for stability.
    """

    def __init__(self, weights: PriorityWeights | None = None) -> None:
        """Initialize the prioritizer.

        Args:
            weights: Custom priority weights. If None, uses defaults
                (alignment=0.5, complexity=0.3, freshness=0.2).
        """
        self.weights = weights or PriorityWeights()

    def prioritize(
        self,
        tasks: list[GeneratedTask],
        introspection_results: list["IntrospectionResult"] | None = None,
    ) -> list[GeneratedTask]:
        """Sort tasks by calculated priority.

        Applies deduplication, calculates final priority scores, then sorts
        by priority descending with created_at ascending as tiebreaker (FIFO).

        Args:
            tasks: List of tasks to prioritize
            introspection_results: Optional introspection results for alignment scoring

        Returns:
            Sorted list of deduplicated tasks
        """
        if not tasks:
            return []

        # Calculate priorities first, then deduplicate (keeps higher priority duplicate)
        for task in tasks:
            task.priority = self._calculate_final_priority(task, introspection_results)

        deduplicated = self._deduplicate(tasks)

        # Sort by priority desc, then created_at asc (FIFO tiebreaker)
        deduplicated.sort(key=lambda t: (-t.priority, t.created_at))
        return deduplicated

    def _score_alignment(
        self,
        task: GeneratedTask,
        introspection_results: list["IntrospectionResult"] | None,
    ) -> float:
        """Score task alignment with introspection insights.

        - introspection source: 1.0 (perfectly aligned)
        - user source: use existing priority (0.0-1.0)
        - other sources: 0.5 (neutral)

        Args:
            task: The task to score
            introspection_results: Not currently used but reserved for
                future semantic similarity scoring

        Returns:
            Alignment score from 0.0 to 1.0
        """
        if task.source == "introspection":
            return 1.0
        if task.source == "user":
            # Preserve user-assigned priority, clamped to valid range
            return min(1.0, max(0.0, task.priority))
        return 0.5

    def _score_complexity(self, task: GeneratedTask) -> float:
        """Score task complexity based on prompt length.

        Shorter prompts suggest simpler, more focused tasks which are
        preferred for incremental progress.

        Scoring brackets:
        - <200 chars: 1.0 (trivial/small)
        - <500 chars: 0.7 (small/medium)
        - <1000 chars: 0.4 (medium/large)
        - >=1000 chars: 0.2 (large/complex)

        Args:
            task: The task to score

        Returns:
            Complexity score from 0.2 to 1.0
        """
        prompt_length = len(task.prompt) if task.prompt else 0

        if prompt_length < 200:
            return 1.0
        elif prompt_length < 500:
            return 0.7
        elif prompt_length < 1000:
            return 0.4
        return 0.2

    def _score_freshness(self, task: GeneratedTask) -> float:
        """Score task freshness with time decay.

        Newer tasks score higher to prevent stale tasks from blocking
        fresh insights. Decay rate is 3% per hour.

        Args:
            task: The task to score

        Returns:
            Freshness score from 0.1 to 1.0
        """
        if not task.created_at:
            return 0.5

        try:
            created = datetime.fromisoformat(task.created_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_hours = (now - created).total_seconds() / 3600

            # Decay rate: 3% per hour, minimum 0.1
            decay_rate = 0.03
            return max(0.1, 1.0 - (age_hours * decay_rate))
        except (ValueError, TypeError):
            return 0.5

    def _calculate_final_priority(
        self,
        task: GeneratedTask,
        introspection_results: list["IntrospectionResult"] | None,
    ) -> float:
        """Calculate weighted final priority score.

        Combines alignment, complexity, and freshness scores using
        configured weights.

        Args:
            task: The task to score
            introspection_results: For alignment scoring

        Returns:
            Final priority score clamped to [0.0, 1.0]
        """
        alignment = self._score_alignment(task, introspection_results)
        complexity = self._score_complexity(task)
        freshness = self._score_freshness(task)

        raw = (
            self.weights.alignment * alignment
            + self.weights.complexity * complexity
            + self.weights.freshness * freshness
        )
        return min(1.0, max(0.0, raw))

    def _deduplicate(self, tasks: list[GeneratedTask]) -> list[GeneratedTask]:
        """Remove duplicate tasks based on prompt prefix similarity.

        Uses the first 100 characters of the prompt (lowercased, stripped)
        as a deduplication key. When duplicates exist, keeps the task with
        higher priority.

        Args:
            tasks: List of tasks to deduplicate

        Returns:
            Deduplicated list of tasks
        """
        seen: dict[str, GeneratedTask] = {}

        for task in tasks:
            # Handle None, empty, and whitespace-only prompts by falling back to task_id
            key = (
                task.prompt[:100].lower().strip()
                if task.prompt and task.prompt.strip()
                else task.task_id
            )
            if key not in seen or task.priority > seen[key].priority:
                seen[key] = task

        return list(seen.values())
