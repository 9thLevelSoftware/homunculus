"""Comparative introspection mode for winner vs loser patch analysis."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models import EpisodeRecord, IntrospectionResult, utc_now
from .base import IntrospectionContext


class ComparativeMode:
    """Compares winning vs losing episodes for the same task to extract patterns."""

    @property
    def name(self) -> str:
        """Mode identifier."""
        return "comparative"

    def run(self, context: IntrospectionContext) -> IntrospectionResult:
        """Execute comparative introspection and return findings."""
        episodes = context.store.load_episodes()
        windowed = episodes[-context.window_size:] if episodes else []

        if not windowed:
            return IntrospectionResult(
                mode=self.name,
                timestamp=utc_now(),
                findings=[],
                summary="No episodes in analysis window",
                metrics={},
                recommendations=["Generate some episodes first to enable comparative analysis."],
            )

        # Group by comparison_group
        grouped = self._group_by_comparison(windowed)

        if not grouped:
            return IntrospectionResult(
                mode=self.name,
                timestamp=utc_now(),
                findings=[],
                summary="No episodes with comparison_group found",
                metrics={},
                recommendations=[
                    "Use comparison_group field in TaskRequest to group related attempts.",
                    "This enables winner/loser analysis for the same task.",
                ],
            )

        # Filter to groups with both winners and losers
        comparable = {
            group_id: eps
            for group_id, eps in grouped.items()
            if self._has_comparison_pair(eps)
        }

        if not comparable:
            return IntrospectionResult(
                mode=self.name,
                timestamp=utc_now(),
                findings=[{
                    "type": "no_comparable_groups",
                    "severity": "info",
                    "detail": f"Found {len(grouped)} groups but none have both winners and losers",
                }],
                summary=f"Found {len(grouped)} task groups but none with both winners and losers",
                metrics={
                    "groups_found": float(len(grouped)),
                    "comparable_groups": 0.0,
                },
                recommendations=[
                    "Continue running episodes to generate winner/loser pairs.",
                    "Ensure tasks are retried with the same comparison_group.",
                ],
            )

        # Analyze each comparable group
        all_findings: list[dict[str, Any]] = []
        all_patterns: list[dict[str, Any]] = []

        for group_id, group_episodes in comparable.items():
            group_result = self._analyze_group(group_id, group_episodes)
            all_findings.extend(group_result["findings"])
            all_patterns.extend(group_result["patterns"])

        # Aggregate patterns across all groups
        aggregated = self._aggregate_patterns(all_patterns)

        summary = self._format_summary(comparable, aggregated)

        return IntrospectionResult(
            mode=self.name,
            timestamp=utc_now(),
            findings=all_findings + aggregated["findings"],
            summary=summary,
            metrics=aggregated["metrics"],
            recommendations=aggregated["recommendations"],
        )

    def _group_by_comparison(
        self, episodes: list[EpisodeRecord]
    ) -> dict[str, list[EpisodeRecord]]:
        """Group episodes by comparison_group, skipping those without one."""
        grouped: dict[str, list[EpisodeRecord]] = defaultdict(list)
        for ep in episodes:
            if ep.comparison_group:
                grouped[ep.comparison_group].append(ep)
        return dict(grouped)

    def _has_comparison_pair(self, episodes: list[EpisodeRecord]) -> bool:
        """Return True if group has at least one winner and one loser."""
        has_winner = any(ep.outcome == "accepted" for ep in episodes)
        has_loser = any(ep.outcome != "accepted" for ep in episodes)
        return has_winner and has_loser

    def _analyze_patch(self, episode: EpisodeRecord) -> dict[str, Any]:
        """Extract patch characteristics from an episode."""
        patch = episode.patch or ""

        # Count lines
        patch_lines = patch.count("\n")

        # Count additions (lines starting with + but not +++)
        additions = 0
        deletions = 0
        for line in patch.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        # Ensure non-negative
        additions = max(0, additions)
        deletions = max(0, deletions)

        return {
            "episode_id": episode.episode_id,
            "outcome": episode.outcome,
            "patch_lines": patch_lines,
            "additions": additions,
            "deletions": deletions,
            "plan_steps": len(episode.plan) if episode.plan else 0,
            "attempt": episode.attempt_index,
            "failure_stage": episode.failure_stage,
        }

    def _compare_patches(
        self, winner: dict[str, Any], loser: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Compare a winner patch with a loser patch, returning insights."""
        insights: list[str] = []

        winner_size = winner["patch_lines"]
        loser_size = loser["patch_lines"]

        # Size comparison
        if loser_size > 0:
            if winner_size < loser_size * 0.7:
                insights.append("Winner patch was significantly smaller")
            elif winner_size > loser_size * 1.3:
                insights.append("Winner patch was larger (more thorough)")

        # Plan comparison
        winner_steps = winner["plan_steps"]
        loser_steps = loser["plan_steps"]
        if loser_steps > 0:
            if winner_steps < loser_steps * 0.7:
                insights.append("Winner had a simpler plan")
            elif winner_steps > loser_steps * 1.3:
                insights.append("Winner had a more detailed plan")

        # Attempt comparison
        if winner["attempt"] < loser["attempt"]:
            insights.append("Winner succeeded on an earlier attempt")

        if not insights:
            return None

        size_ratio = winner_size / loser_size if loser_size > 0 else 1.0

        return {
            "insight": "; ".join(insights),
            "metric": round(size_ratio, 2),
        }

    def _analyze_group(
        self, group_id: str, episodes: list[EpisodeRecord]
    ) -> dict[str, Any]:
        """Analyze a group of episodes with winners and losers."""
        winners = [ep for ep in episodes if ep.outcome == "accepted"]
        losers = [ep for ep in episodes if ep.outcome != "accepted"]

        findings: list[dict[str, Any]] = []
        patterns: list[dict[str, Any]] = []

        # Group stats finding
        total = len(episodes)
        win_ratio = len(winners) / total if total > 0 else 0.0
        findings.append({
            "type": "group_stats",
            "group_id": group_id,
            "winners": len(winners),
            "losers": len(losers),
            "win_ratio": round(win_ratio, 2),
            "severity": "info",
            "detail": f"Task '{group_id}': {len(winners)} winners, {len(losers)} losers ({win_ratio * 100:.0f}% success)",
        })

        # Analyze patches
        winner_analyses = [self._analyze_patch(ep) for ep in winners]
        loser_analyses = [self._analyze_patch(ep) for ep in losers]

        # Store for aggregation
        for wa in winner_analyses:
            wa["is_winner"] = True
            patterns.append(wa)
        for la in loser_analyses:
            la["is_winner"] = False
            patterns.append(la)

        # Compare first winner vs first loser
        if winner_analyses and loser_analyses:
            comparison = self._compare_patches(winner_analyses[0], loser_analyses[0])
            if comparison:
                findings.append({
                    "type": "patch_comparison",
                    "group_id": group_id,
                    "severity": "info",
                    "insight": comparison["insight"],
                    "size_ratio": comparison["metric"],
                    "detail": f"Comparison for '{group_id}': {comparison['insight']}",
                })

        return {"findings": findings, "patterns": patterns}

    def _aggregate_patterns(
        self, patterns: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Aggregate patterns across all groups to find common signals."""
        findings: list[dict[str, Any]] = []
        recommendations: list[str] = []

        if not patterns:
            return {
                "findings": findings,
                "metrics": {},
                "recommendations": ["No patterns to analyze yet."],
            }

        # Separate winners and losers
        winners = [p for p in patterns if p.get("is_winner")]
        losers = [p for p in patterns if not p.get("is_winner")]

        # Calculate averages
        avg_winner_lines = (
            sum(w["patch_lines"] for w in winners) / len(winners) if winners else 0.0
        )
        avg_loser_lines = (
            sum(l["patch_lines"] for l in losers) / len(losers) if losers else 0.0
        )
        avg_winner_steps = (
            sum(w["plan_steps"] for w in winners) / len(winners) if winners else 0.0
        )
        avg_loser_steps = (
            sum(l["plan_steps"] for l in losers) / len(losers) if losers else 0.0
        )

        metrics: dict[str, float] = {
            "avg_winner_lines": round(avg_winner_lines, 1),
            "avg_loser_lines": round(avg_loser_lines, 1),
            "avg_winner_steps": round(avg_winner_steps, 1),
            "avg_loser_steps": round(avg_loser_steps, 1),
            "total_winners": float(len(winners)),
            "total_losers": float(len(losers)),
        }

        # Size pattern
        if avg_loser_lines > 0 and avg_winner_lines < avg_loser_lines * 0.8:
            findings.append({
                "type": "size_pattern",
                "severity": "info",
                "detail": f"Winners average {avg_winner_lines:.0f} lines vs losers {avg_loser_lines:.0f} lines",
            })
            recommendations.append(
                "Winning patches tend to be smaller. Consider more focused, minimal changes."
            )

        # Plan pattern
        if avg_loser_steps > 0 and avg_winner_steps < avg_loser_steps * 0.8:
            findings.append({
                "type": "plan_pattern",
                "severity": "info",
                "detail": f"Winners average {avg_winner_steps:.0f} plan steps vs losers {avg_loser_steps:.0f}",
            })
            recommendations.append(
                "Winning plans tend to be simpler. Consider fewer, more targeted plan steps."
            )

        # Failure stage pattern
        failure_stages = [l.get("failure_stage") for l in losers if l.get("failure_stage")]
        if failure_stages:
            from collections import Counter
            stage_counts = Counter(failure_stages)
            most_common_stage, most_common_count = stage_counts.most_common(1)[0]
            stage_pct = most_common_count / len(losers) * 100

            findings.append({
                "type": "failure_stage_pattern",
                "severity": "warning" if stage_pct > 50 else "info",
                "detail": f"Most common failure stage: '{most_common_stage}' ({stage_pct:.0f}% of losers)",
            })
            metrics["dominant_failure_stage_pct"] = round(stage_pct, 1)

            recommendations.append(
                f"Focus on improving '{most_common_stage}' stage - it's where {stage_pct:.0f}% of failures occur."
            )

        if not recommendations:
            recommendations.append("Continue gathering data to identify stronger patterns.")

        return {
            "findings": findings,
            "metrics": metrics,
            "recommendations": recommendations,
        }

    def _format_summary(
        self,
        comparable: dict[str, list[EpisodeRecord]],
        aggregated: dict[str, Any],
    ) -> str:
        """Format a human-readable summary of the comparative analysis."""
        total_groups = len(comparable)
        metrics = aggregated.get("metrics", {})
        total_winners = int(metrics.get("total_winners", 0))
        total_losers = int(metrics.get("total_losers", 0))
        avg_winner_lines = metrics.get("avg_winner_lines", 0)

        return (
            f"Compared {total_groups} task groups: "
            f"{total_winners} winners vs {total_losers} losers. "
            f"Avg winning patch: {avg_winner_lines:.0f} lines."
        )
