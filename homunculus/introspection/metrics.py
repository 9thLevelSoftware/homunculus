"""Metrics introspection mode for quantitative episode analysis."""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..models import EpisodeRecord, IntrospectionResult, utc_now
from .base import IntrospectionContext

# Threshold constants for metrics analysis
SUCCESS_RATE_HEALTHY = 0.7  # Above this is considered healthy
SUCCESS_RATE_LOW = 0.5  # Below this triggers recommendation
ERROR_RATE_CRITICAL = 0.1  # Above this triggers critical finding
RETRY_RATE_HIGH = 0.3  # Above this triggers warning finding
FAILURE_STAGE_HIGH = 0.2  # Above this for a single stage triggers recommendation
FAILURE_STAGE_WARNING = 0.1  # Above this triggers warning severity in findings


class MetricsMode:
    """Analyzes episode records to compute quantitative performance signals."""

    @property
    def name(self) -> str:
        """Mode identifier."""
        return "metrics"

    def run(self, context: IntrospectionContext) -> IntrospectionResult:
        """Execute metrics introspection and return findings."""
        episodes = context.store.load_episodes()
        windowed = episodes[-context.window_size:] if episodes else []

        if not windowed:
            return IntrospectionResult(
                mode=self.name,
                timestamp=utc_now(),
                findings=[],
                summary="No episodes in analysis window",
                metrics={},
                recommendations=["Generate some episodes first to enable metrics analysis."],
            )

        metrics = self._compute_metrics(windowed)
        findings = self._generate_findings(metrics, len(windowed))
        recommendations = self._generate_recommendations(metrics)
        summary = self._format_summary(metrics, len(windowed))

        return IntrospectionResult(
            mode=self.name,
            timestamp=utc_now(),
            findings=findings,
            summary=summary,
            metrics=metrics,
            recommendations=recommendations,
        )

    def _compute_metrics(self, episodes: list[EpisodeRecord]) -> dict[str, float]:
        """Compute all metrics from episodes."""
        total = len(episodes)
        if total == 0:
            return {}

        # Outcome counts
        outcome_counts = Counter(ep.outcome for ep in episodes)
        accepted = outcome_counts.get("accepted", 0)
        reverted = outcome_counts.get("reverted", 0)
        error = outcome_counts.get("error", 0)
        blocked = outcome_counts.get("blocked", 0)

        # Retry statistics
        # avg_attempts_when_retried: Average attempt_index for episodes that
        # required retries (attempt_index > 1). This metric captures the typical
        # number of attempts needed when the first attempt fails.
        retry_episodes = [ep for ep in episodes if ep.attempt_index > 1]
        retry_count = len(retry_episodes)
        avg_attempts_when_retried = (
            sum(ep.attempt_index for ep in retry_episodes) / retry_count
            if retry_count > 0
            else 0.0
        )

        # Source distribution
        self_generated = sum(1 for ep in episodes if ep.source == "self-generated")

        # Failure stage distribution
        failure_stages: Counter[str] = Counter()
        for ep in episodes:
            if ep.failure_stage:
                failure_stages[ep.failure_stage] += 1

        # Build metrics dict
        metrics: dict[str, float] = {
            "success_rate": round(accepted / total, 3),
            "revert_rate": round(reverted / total, 3),
            "error_rate": round(error / total, 3),
            "blocked_rate": round(blocked / total, 3),
            "avg_attempts_when_retried": round(avg_attempts_when_retried, 3),
            "retry_rate": round(retry_count / total, 3),
            "self_generated_ratio": round(self_generated / total, 3),
        }

        # Add failure stage distribution
        for stage, count in failure_stages.items():
            metrics[f"failure_{stage}"] = round(count / total, 3)

        return metrics

    def _generate_findings(
        self, metrics: dict[str, float], episode_count: int
    ) -> list[dict[str, Any]]:
        """Generate structured findings from computed metrics."""
        findings: list[dict[str, Any]] = []

        # Success rate finding
        success_rate = metrics.get("success_rate", 0.0)
        findings.append({
            "type": "success_rate",
            "value": success_rate,
            "severity": "info" if success_rate >= SUCCESS_RATE_HEALTHY else "warning",
            "detail": f"Success rate is {success_rate * 100:.1f}% across {episode_count} episodes",
        })

        # High error rate finding
        error_rate = metrics.get("error_rate", 0.0)
        if error_rate > ERROR_RATE_CRITICAL:
            findings.append({
                "type": "high_error_rate",
                "value": error_rate,
                "severity": "critical",
                "detail": f"Error rate of {error_rate * 100:.1f}% exceeds {ERROR_RATE_CRITICAL * 100:.0f}% threshold",
            })

        # High retry rate finding
        retry_rate = metrics.get("retry_rate", 0.0)
        if retry_rate > RETRY_RATE_HIGH:
            findings.append({
                "type": "high_retry_rate",
                "value": retry_rate,
                "severity": "warning",
                "detail": f"Retry rate of {retry_rate * 100:.1f}% exceeds {RETRY_RATE_HIGH * 100:.0f}% threshold",
            })

        # Failure concentration finding
        failure_metrics = {
            k: v for k, v in metrics.items() if k.startswith("failure_")
        }
        if failure_metrics:
            max_stage = max(failure_metrics.items(), key=lambda x: x[1])
            stage_name = max_stage[0].replace("failure_", "")
            findings.append({
                "type": "failure_concentration",
                "value": max_stage[1],
                "severity": "warning" if max_stage[1] > FAILURE_STAGE_WARNING else "info",
                "detail": f"Most failures occur at '{stage_name}' stage ({max_stage[1] * 100:.1f}% of episodes)",
            })

        return findings

    def _generate_recommendations(self, metrics: dict[str, float]) -> list[str]:
        """Generate actionable recommendations based on metric thresholds."""
        recommendations: list[str] = []

        success_rate = metrics.get("success_rate", 1.0)
        error_rate = metrics.get("error_rate", 0.0)
        retry_rate = metrics.get("retry_rate", 0.0)
        failure_execute = metrics.get("failure_execute", 0.0)
        failure_plan = metrics.get("failure_plan", 0.0)

        if success_rate < SUCCESS_RATE_LOW:
            recommendations.append(
                f"Success rate below {SUCCESS_RATE_LOW * 100:.0f}%. Consider reviewing recent failures for common patterns."
            )

        if error_rate > ERROR_RATE_CRITICAL:
            recommendations.append(
                "High error rate detected. Check for infrastructure issues or invalid task prompts."
            )

        if retry_rate > RETRY_RATE_HIGH:
            recommendations.append(
                "Many tasks require retries. Consider improving initial plan generation."
            )

        if failure_execute > FAILURE_STAGE_HIGH:
            recommendations.append(
                "Execute stage failures high. Review patch application and worktree isolation."
            )

        if failure_plan > FAILURE_STAGE_HIGH:
            recommendations.append(
                "Plan stage failures high. Teacher model may need prompt adjustments."
            )

        if not recommendations:
            recommendations.append("Metrics look healthy. Continue current approach.")

        return recommendations

    def _format_summary(self, metrics: dict[str, float], episode_count: int) -> str:
        """Format a human-readable summary of the metrics."""
        success_pct = metrics.get("success_rate", 0.0) * 100
        retry_pct = metrics.get("retry_rate", 0.0) * 100
        self_gen_pct = metrics.get("self_generated_ratio", 0.0) * 100

        return (
            f"Analyzed {episode_count} episodes: "
            f"{success_pct:.0f}% success rate, "
            f"{retry_pct:.0f}% retry rate, "
            f"{self_gen_pct:.0f}% self-generated"
        )
