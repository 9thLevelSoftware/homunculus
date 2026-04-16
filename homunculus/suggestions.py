from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .models import GeneratedTask, utc_now

if TYPE_CHECKING:
    from .models import IntrospectionResult


class SuggestionReader:
    """Reads and parses seed task suggestions from markdown files."""

    PRIORITY_MAP = {
        "HIGH": 1.0,
        "MEDIUM": 0.5,
        "LOW": 0.2,
    }

    # Keywords derived from introspection mode outputs
    RESONANCE_KEYWORDS = {
        "error": ["error", "exception", "handling", "try", "catch", "raise", "fail", "retry"],
        "testing": ["test", "coverage", "assert", "unittest", "pytest", "mock", "gap", "suite"],
        "async": ["async", "await", "concurrent", "parallel", "thread", "coroutine"],
        "performance": ["performance", "speed", "optimize", "cache", "fast", "slow", "memory"],
        "security": ["security", "auth", "permission", "token", "secret", "credential"],
        "documentation": ["doc", "readme", "comment", "docstring", "todo"],
        "refactor": ["refactor", "clean", "simplify", "extract", "rename", "consolidate"],
        "patching": ["patch", "diff", "change", "modify", "edit", "fix"],
        "planning": ["plan", "step", "approach", "strategy", "design"],
        "lifecycle": ["execute", "reflect", "curate", "assess", "preflight"],
    }

    def __init__(self, suggestions_dir: Path) -> None:
        self.suggestions_dir = Path(suggestions_dir)
        self.archive_dir = self.suggestions_dir / "archive"

    def read_pending(self) -> list[GeneratedTask]:
        """Read all pending suggestion files and convert to tasks."""
        if not self.suggestions_dir.exists():
            return []

        tasks = []
        for md_file in self.suggestions_dir.glob("*.md"):
            if md_file.name.startswith("."):
                continue
            task = self._parse_suggestion(md_file)
            if task:
                tasks.append(task)

        # Sort by priority descending
        tasks.sort(key=lambda t: t.priority, reverse=True)
        return tasks

    def archive(self, filename: str, outcome: str) -> None:
        """Move a processed suggestion to the archive directory."""
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        source = self.suggestions_dir / filename
        if not source.exists():
            return

        stem = source.stem
        dest = self.archive_dir / f"{stem}.{outcome}.md"
        source.rename(dest)

    def _parse_suggestion(self, md_file: Path) -> GeneratedTask | None:
        """Parse a suggestion markdown file into a GeneratedTask."""
        content = md_file.read_text(encoding="utf-8")

        # Extract sections
        title = self._extract_title(content)
        priority_str = self._extract_section(content, "Priority")
        what = self._extract_section(content, "What")
        why = self._extract_section(content, "Why")
        success_criteria = self._extract_section(content, "Success Criteria")
        hints = self._extract_section(content, "Hints")

        if not what:
            return None

        # Build prompt from sections
        prompt_parts = []
        if title:
            prompt_parts.append(f"# {title}")
        prompt_parts.append(what)
        if why:
            prompt_parts.append(f"\n## Why\n{why}")
        if hints:
            prompt_parts.append(f"\n## Hints\n{hints}")

        priority = self.PRIORITY_MAP.get(priority_str.strip().upper(), 0.5) if priority_str else 0.5

        return GeneratedTask(
            task_id=f"suggestion-{uuid.uuid4().hex[:8]}",
            source="user",
            prompt="\n".join(prompt_parts),
            priority=priority,
            success_criteria=success_criteria or "",
            context={"filename": md_file.name},
            created_at=utc_now(),
        )

    def _extract_keywords(self, text: str) -> set[str]:
        """Extract normalized keyword categories from text."""
        text_lower = text.lower()
        found: set[str] = set()
        for category, keywords in self.RESONANCE_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                found.add(category)
        return found

    def score_resonance(
        self,
        task: GeneratedTask,
        introspection_results: list["IntrospectionResult"],
    ) -> float:
        """Score how well a task aligns with current introspection insights.

        Returns: Resonance score from 0.0 (no alignment) to 1.0 (perfect alignment)
        """
        if not introspection_results:
            return 0.0

        task_keywords = self._extract_keywords(task.prompt)
        if task.success_criteria:
            task_keywords |= self._extract_keywords(task.success_criteria)

        if not task_keywords:
            return 0.0

        total_score = 0.0
        total_weight = 0.0

        for i, result in enumerate(introspection_results):
            # Smooth exponential decay: 0.4 + 0.6 * (0.8 ** i)
            # i=0: 1.0, i=1: 0.88, i=2: 0.784, i=3: 0.707, ...
            # Asymptotes to 0.4 smoothly
            weight = 0.4 + 0.6 * (0.8 ** i)

            result_keywords: set[str] = set()
            for rec in result.recommendations:
                result_keywords |= self._extract_keywords(rec)
            for finding in result.findings:
                if "area" in finding:
                    result_keywords |= self._extract_keywords(str(finding["area"]))
                if "description" in finding:
                    result_keywords |= self._extract_keywords(str(finding["description"]))

            if result_keywords:
                intersection = task_keywords & result_keywords
                union = task_keywords | result_keywords
                similarity = len(intersection) / len(union) if union else 0.0
                total_score += similarity * weight
                total_weight += weight

        return total_score / total_weight if total_weight > 0 else 0.0

    def read_pending_with_resonance(
        self,
        introspection_results: list["IntrospectionResult"],
        resonance_boost: float = 0.3,
    ) -> list[GeneratedTask]:
        """Read pending suggestions with priority boosted by resonance.

        Args:
            introspection_results: Recent introspection results for scoring
            resonance_boost: Maximum boost to add (0.0-1.0)

        Returns:
            List of tasks with adjusted priorities, sorted by priority descending
        """
        tasks = self.read_pending()

        for task in tasks:
            resonance = self.score_resonance(task, introspection_results)
            boost = resonance * resonance_boost
            task.priority = min(1.0, task.priority + boost)

        tasks.sort(key=lambda t: t.priority, reverse=True)
        return tasks

    def _extract_title(self, content: str) -> str:
        """Extract the H1 title from markdown."""
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _extract_section(self, content: str, section_name: str) -> str:
        """Extract content under a ## heading."""
        pattern = rf"^##\s+{re.escape(section_name)}\s*\n(.*?)(?=^##|\Z)"
        match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else ""
