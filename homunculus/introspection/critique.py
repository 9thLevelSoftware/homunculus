"""Critique introspection mode using LLM to analyze episode patterns."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ..models import EpisodeRecord, IntrospectionResult, TaskRequest, utc_now
from .base import IntrospectionContext

if TYPE_CHECKING:
    from ..orchestrator.teacher import OpenAICompatibleTeacher


# Prompt for the teacher to analyze episodes and produce structured output
ANALYSIS_PROMPT = """You are analyzing a series of coding agent episodes to identify patterns, weaknesses, and strengths.

Each episode summary below shows:
- Episode ID and outcome (OK=accepted, FAIL=reverted, ERR=error, BLOCK=blocked)
- Task prompt (truncated)
- Plan step count
- For failures: failure stage and error message
- Patch preview (truncated)

Analyze these episodes and return a JSON object with exactly this structure:
{{
  "patterns": [
    {{"pattern": "description", "frequency": "common|occasional|rare", "severity": "low|medium|high", "examples": ["ep_id1", "ep_id2"]}}
  ],
  "weaknesses": [
    {{"area": "area name", "description": "what's weak", "impact": "how it affects outcomes", "recommendation": "how to improve"}}
  ],
  "strengths": ["strength 1", "strength 2"],
  "summary": "One paragraph overall assessment of the agent's performance and key observations."
}}

EPISODES:
{episode_summaries}

Return ONLY the JSON object, no other text."""


class CritiqueMode:
    """Uses teacher model to analyze recent episodes and identify patterns."""

    def __init__(self, teacher: "OpenAICompatibleTeacher | None" = None) -> None:
        """Initialize with optional teacher for dependency injection.

        Args:
            teacher: OpenAI-compatible teacher client. If None, will be created
                from config when run() is called.
        """
        self._teacher = teacher

    @property
    def name(self) -> str:
        """Mode identifier."""
        return "critique"

    def run(self, context: IntrospectionContext) -> IntrospectionResult:
        """Execute critique introspection using the teacher model.

        Analyzes recent episodes to identify patterns, weaknesses, and strengths
        by sending summaries to the teacher model for analysis.
        """
        # Check if critique is enabled
        if not context.config.introspection.critique_enabled:
            return IntrospectionResult(
                mode=self.name,
                timestamp=utc_now(),
                findings=[],
                summary="Critique mode disabled in config",
                metrics={},
                recommendations=[],
            )

        # Load recent episodes
        episodes = self._load_recent_episodes(context)

        # Require minimum episodes for meaningful analysis
        if len(episodes) < 3:
            return IntrospectionResult(
                mode=self.name,
                timestamp=utc_now(),
                findings=[],
                summary=f"Insufficient episodes for critique (have {len(episodes)}, need 3+)",
                metrics={"episodes_analyzed": float(len(episodes))},
                recommendations=["Run more episodes to enable pattern analysis."],
            )

        # Get or create teacher
        teacher = self._get_teacher(context)

        # Analyze episodes
        analysis = self._analyze_episodes(episodes, teacher, context.cycle_number)

        # Build and return result
        return self._build_result(analysis, len(episodes))

    def _get_teacher(self, context: IntrospectionContext) -> "OpenAICompatibleTeacher":
        """Get teacher instance, creating from config if needed."""
        if self._teacher is not None:
            return self._teacher
        # Import here to avoid circular imports
        from ..orchestrator.teacher import OpenAICompatibleTeacher
        return OpenAICompatibleTeacher(context.config.teacher)

    def _load_recent_episodes(self, context: IntrospectionContext) -> list[EpisodeRecord]:
        """Load last N episodes from store based on window_size."""
        episodes = context.store.load_episodes()
        return episodes[-context.window_size:] if episodes else []

    def _summarize_episode(self, ep: EpisodeRecord) -> str:
        """Create concise summary of an episode for LLM analysis.

        Args:
            ep: The episode record to summarize

        Returns:
            Human-readable summary string
        """
        # Outcome emoji for quick visual parsing
        outcome_map = {
            "accepted": "OK",
            "reverted": "FAIL",
            "error": "ERR",
            "blocked": "BLOCK",
        }
        outcome_str = outcome_map.get(ep.outcome, ep.outcome.upper())

        # Build summary lines
        lines = [
            f"[{ep.episode_id[:8]}] {outcome_str}",
            f"  Task: {ep.prompt[:100]}{'...' if len(ep.prompt) > 100 else ''}",
            f"  Plan steps: {len(ep.plan)}",
        ]

        # Add failure info if applicable
        if ep.outcome != "accepted":
            if ep.failure_stage:
                lines.append(f"  Failure stage: {ep.failure_stage}")
            if ep.error_message:
                err_truncated = ep.error_message[:100]
                if len(ep.error_message) > 100:
                    err_truncated += "..."
                lines.append(f"  Error: {err_truncated}")

        # Add patch preview if available
        if ep.patch:
            patch_preview = ep.patch[:200]
            if len(ep.patch) > 200:
                patch_preview += "\n  [truncated]"
            lines.append(f"  Patch preview:\n  {patch_preview}")

        return "\n".join(lines)

    def _analyze_episodes(
        self,
        episodes: list[EpisodeRecord],
        teacher: "OpenAICompatibleTeacher",
        cycle_number: int,
    ) -> dict[str, Any]:
        """Send episode summaries to teacher for analysis.

        Args:
            episodes: Episodes to analyze
            teacher: Teacher client for API calls
            cycle_number: Current introspection cycle number

        Returns:
            Parsed analysis dict with patterns, weaknesses, strengths, summary
        """
        # Limit to prevent token overflow
        limited = episodes[-20:]

        # Format summaries
        summaries = [self._summarize_episode(ep) for ep in limited]
        formatted = "\n---\n".join(summaries)

        # Construct the analysis prompt
        prompt = ANALYSIS_PROMPT.format(episode_summaries=formatted)

        # Create task request
        task = TaskRequest(
            task_id=f"introspection-critique-{cycle_number}",
            workspace="self",
            prompt=prompt,
            metadata={"introspection_mode": "critique"},
        )

        try:
            # Call teacher API
            response = teacher.generate(task, memories=[], student_hint=None)

            # Extract content from response
            content = self._extract_response_content(response)

            # Parse JSON from content
            return self._parse_analysis_json(content)

        except Exception as e:
            # Return error analysis on any failure
            return {
                "patterns": [],
                "weaknesses": [],
                "strengths": [],
                "summary": f"Analysis failed: {str(e)[:200]}",
                "error": str(e),
            }

    def _extract_response_content(self, response: Any) -> str:
        """Extract content string from teacher response.

        Args:
            response: TeacherResponse object

        Returns:
            Content string from response
        """
        # Try OpenAI-style: choices[0].message.content
        raw = response.raw
        if raw and "choices" in raw:
            try:
                content = raw["choices"][0]["message"]["content"]
                if isinstance(content, str):
                    return content
            except (KeyError, IndexError, TypeError):
                pass

        # Fall back to rationale
        if response.rationale:
            return response.rationale

        # Fall back to stringified plan
        if response.plan:
            return str(response.plan)

        return ""

    def _parse_analysis_json(self, content: str) -> dict[str, Any]:
        """Parse JSON from content string.

        Args:
            content: String that may contain JSON

        Returns:
            Parsed dict, or error dict on failure
        """
        if not content:
            return {
                "patterns": [],
                "weaknesses": [],
                "strengths": [],
                "summary": "Empty response from teacher",
                "error": "empty_response",
            }

        # Try direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON object using regex
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return {
            "patterns": [],
            "weaknesses": [],
            "strengths": [],
            "summary": f"Failed to parse JSON from response: {content[:100]}...",
            "error": "json_parse_error",
        }

    def _build_result(
        self, analysis: dict[str, Any], episode_count: int
    ) -> IntrospectionResult:
        """Convert analysis dict to IntrospectionResult.

        Args:
            analysis: Dict with patterns, weaknesses, strengths, summary
            episode_count: Number of episodes that were analyzed

        Returns:
            Structured IntrospectionResult
        """
        findings: list[dict[str, Any]] = []
        recommendations: list[str] = []

        # Convert patterns to findings
        patterns = analysis.get("patterns", [])
        for pattern in patterns:
            severity_map = {"high": "warning", "medium": "warning", "low": "info"}
            raw_severity = pattern.get("severity", "low")
            findings.append({
                "type": "pattern",
                "pattern": pattern.get("pattern", ""),
                "frequency": pattern.get("frequency", "unknown"),
                "severity": severity_map.get(raw_severity, "info"),
                "examples": pattern.get("examples", []),
            })

        # Convert weaknesses to findings and recommendations
        weaknesses = analysis.get("weaknesses", [])
        for weakness in weaknesses:
            findings.append({
                "type": "weakness",
                "area": weakness.get("area", ""),
                "description": weakness.get("description", ""),
                "impact": weakness.get("impact", ""),
                "severity": "warning",
            })
            recommendation = weakness.get("recommendation")
            if recommendation:
                recommendations.append(recommendation)

        # Convert strengths to positive findings
        strengths = analysis.get("strengths", [])
        for strength in strengths:
            findings.append({
                "type": "strength",
                "description": strength,
                "severity": "info",
            })

        # Add error finding if analysis failed
        if "error" in analysis:
            findings.append({
                "type": "analysis_error",
                "error": analysis["error"],
                "severity": "warning",
            })

        # Build metrics
        metrics = {
            "patterns_found": float(len(patterns)),
            "weaknesses_found": float(len(weaknesses)),
            "strengths_found": float(len(strengths)),
            "episodes_analyzed": float(episode_count),
        }

        # Build summary
        summary = analysis.get("summary", "")
        if not summary:
            summary = (
                f"Analyzed {episode_count} episodes. "
                f"Found {len(patterns)} patterns, "
                f"{len(weaknesses)} weaknesses, "
                f"{len(strengths)} strengths."
            )
        # Truncate summary to 500 chars
        if len(summary) > 500:
            summary = summary[:497] + "..."

        return IntrospectionResult(
            mode=self.name,
            timestamp=utc_now(),
            findings=findings,
            summary=summary,
            metrics=metrics,
            recommendations=recommendations,
        )
