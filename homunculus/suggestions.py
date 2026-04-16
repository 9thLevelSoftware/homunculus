from __future__ import annotations

import re
import uuid
from pathlib import Path

from .models import GeneratedTask, utc_now


class SuggestionReader:
    """Reads and parses seed task suggestions from markdown files."""

    PRIORITY_MAP = {
        "HIGH": 1.0,
        "MEDIUM": 0.5,
        "LOW": 0.2,
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

    def _extract_title(self, content: str) -> str:
        """Extract the H1 title from markdown."""
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _extract_section(self, content: str, section_name: str) -> str:
        """Extract content under a ## heading."""
        pattern = rf"^##\s+{re.escape(section_name)}\s*\n(.*?)(?=^##|\Z)"
        match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else ""
