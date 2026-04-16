"""Task generator that converts introspection findings into actionable tasks."""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from ..models import GeneratedTask, IntrospectionResult, utc_now

if TYPE_CHECKING:
    from ..storage import ArtifactStore

logger = logging.getLogger(__name__)


class TaskGenerator:
    """Converts introspection findings into actionable tasks.

    Each introspection mode produces different finding types that require
    mode-specific task generation logic. The generator uses defensive parsing
    to handle malformed or incomplete findings gracefully.
    """

    # Mode weights for priority calculation - determines base urgency
    MODE_WEIGHTS: dict[str, float] = {
        "metrics": 1.0,       # Most actionable - quantitative performance gaps
        "critique": 0.9,      # LLM-identified issues - qualitative insights
        "coverage": 0.7,      # Concrete but lower urgency - code quality
        "comparative": 0.6,   # Insights for improvement - pattern recognition
    }

    # Severity base scores for priority calculation
    SEVERITY_SCORES: dict[str, float] = {
        "critical": 0.95,
        "high": 0.8,
        "warning": 0.7,
        "medium": 0.5,
        "low": 0.3,
        "info": 0.2,
    }

    def __init__(self, store: "ArtifactStore | None" = None) -> None:
        """Initialize the task generator.

        Args:
            store: Optional artifact store for persistence. Not currently used
                but reserved for future features like deduplication.
        """
        self.store = store

    def generate_from_introspection(
        self,
        results: list[IntrospectionResult],
        max_tasks: int = 5,
    ) -> list[GeneratedTask]:
        """Generate tasks from recent introspection results.

        Processes each introspection result using mode-specific generators,
        then sorts by priority and returns the top N tasks.

        Args:
            results: List of IntrospectionResult objects to process
            max_tasks: Maximum number of tasks to return (default: 5)

        Returns:
            List of GeneratedTask objects sorted by priority (highest first)
        """
        if not results:
            return []

        all_tasks: list[GeneratedTask] = []

        for result in results:
            try:
                tasks = self._generate_for_result(result)
                all_tasks.extend(tasks)
            except Exception as e:
                logger.warning(
                    "Failed to generate tasks for %s introspection: %s",
                    result.mode,
                    e,
                )
                continue

        # Sort by priority (highest first) and limit
        all_tasks.sort(key=lambda t: t.priority, reverse=True)
        return all_tasks[:max_tasks]

    def _generate_for_result(
        self, result: IntrospectionResult
    ) -> list[GeneratedTask]:
        """Route to mode-specific generator.

        Args:
            result: IntrospectionResult to process

        Returns:
            List of tasks generated from this result
        """
        generators = {
            "metrics": self._generate_from_metrics,
            "critique": self._generate_from_critique,
            "coverage": self._generate_from_coverage,
            "comparative": self._generate_from_comparative,
        }

        generator = generators.get(result.mode)
        if generator is None:
            logger.warning("Unknown introspection mode: %s", result.mode)
            return []

        return generator(result)

    # ─────────────────────────────────────────────────────────────────────────
    # Helper Methods
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_finding_field(
        self, finding: dict, field: str, default: Any = None
    ) -> Any:
        """Safely extract field from finding dict.

        Args:
            finding: The finding dictionary to extract from
            field: The field name to extract
            default: Default value if field is missing

        Returns:
            The field value or default
        """
        return finding.get(field, default)

    def _infer_severity(self, finding: dict) -> str:
        """Infer severity from finding status, type, or other fields.

        Checks multiple fields to determine severity:
        1. Explicit 'severity' field
        2. 'status' field (critical, warning, info)
        3. 'type' field heuristics (weakness -> high, gap -> medium)

        Args:
            finding: The finding dictionary

        Returns:
            Severity string: critical, high, warning, medium, low, or info
        """
        # Check explicit severity first
        if severity := finding.get("severity"):
            return severity

        # Check status field (used by metrics mode)
        if status := finding.get("status"):
            return status

        # Infer from type field
        finding_type = finding.get("type", "")
        if finding_type == "weakness":
            return "high"
        if finding_type == "gap":
            return "medium"
        if finding_type in ("high_error_rate",):
            return "critical"
        if finding_type in ("high_retry_rate", "failure_concentration"):
            return "warning"

        # Check impact field if present
        impact = finding.get("impact", "")
        if impact == "high":
            return "high"
        if impact == "medium":
            return "medium"

        return "low"

    def _calculate_priority(self, finding: dict, mode: str) -> float:
        """Calculate priority from finding severity and mode weight.

        Priority is computed as: severity_score * mode_weight
        Result is clamped to [0.0, 1.0].

        Args:
            finding: The finding dictionary
            mode: The introspection mode name

        Returns:
            Priority score between 0.0 and 1.0
        """
        severity = self._infer_severity(finding)
        base = self.SEVERITY_SCORES.get(severity, 0.5)
        weight = self.MODE_WEIGHTS.get(mode, 0.5)
        return min(1.0, base * weight)

    def _make_task_id(self) -> str:
        """Generate a unique task ID."""
        return f"task-{uuid.uuid4().hex[:12]}"

    # ─────────────────────────────────────────────────────────────────────────
    # Mode-Specific Generators
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_from_metrics(
        self, result: IntrospectionResult
    ) -> list[GeneratedTask]:
        """Generate tasks from metrics introspection findings.

        Handles finding types:
        - success_rate: Low success rate requires practice
        - high_error_rate: Error patterns need investigation
        - high_retry_rate: Plan quality needs improvement
        - failure_concentration: Specific stage needs attention

        Args:
            result: MetricsMode introspection result

        Returns:
            List of generated tasks
        """
        tasks: list[GeneratedTask] = []

        for finding in result.findings:
            try:
                task = self._process_metrics_finding(finding, result)
                if task:
                    tasks.append(task)
            except Exception as e:
                logger.warning("Failed to process metrics finding: %s", e)
                continue

        return tasks

    def _process_metrics_finding(
        self, finding: dict, result: IntrospectionResult
    ) -> GeneratedTask | None:
        """Process a single metrics finding into a task.

        Args:
            finding: The metrics finding dict
            result: The parent IntrospectionResult

        Returns:
            GeneratedTask or None if finding should be skipped
        """
        finding_type = self._extract_finding_field(finding, "type", "")
        severity = self._infer_severity(finding)

        # Only generate tasks for actionable findings
        if severity not in ("critical", "high", "warning"):
            return None

        value = self._extract_finding_field(finding, "value", 0.0)

        if finding_type == "success_rate":
            # Low success rate - generate practice task
            if value < 0.7:
                return GeneratedTask(
                    task_id=self._make_task_id(),
                    source="introspection",
                    prompt=self._format_success_rate_prompt(value),
                    priority=self._calculate_priority(finding, result.mode),
                    introspection_mode=result.mode,
                    context={"finding_type": finding_type, "value": value},
                    estimated_complexity="medium",
                    success_criteria="Next introspection cycle shows improved success rate above 70%",
                )

        elif finding_type == "high_error_rate":
            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_error_rate_prompt(value),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={"finding_type": finding_type, "value": value},
                estimated_complexity="medium",
                success_criteria="Error rate drops below 10% in next cycle",
            )

        elif finding_type == "high_retry_rate":
            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_retry_rate_prompt(value),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={"finding_type": finding_type, "value": value},
                estimated_complexity="small",
                success_criteria="Retry rate drops below 30% in next cycle",
            )

        elif finding_type == "failure_concentration":
            detail = self._extract_finding_field(finding, "detail", "")
            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_failure_stage_prompt(detail),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={"finding_type": finding_type, "detail": detail},
                estimated_complexity="medium",
                success_criteria="Failure concentration at this stage decreases",
            )

        return None

    def _generate_from_critique(
        self, result: IntrospectionResult
    ) -> list[GeneratedTask]:
        """Generate tasks from critique introspection findings.

        Handles finding types:
        - pattern: Recurring pattern identified by LLM
        - weakness: Area needing improvement with recommendation
        - strength: Positive finding (no task generated)
        - analysis_error: Skip - indicates LLM failure

        Args:
            result: CritiqueMode introspection result

        Returns:
            List of generated tasks
        """
        tasks: list[GeneratedTask] = []

        for finding in result.findings:
            try:
                task = self._process_critique_finding(finding, result)
                if task:
                    tasks.append(task)
            except Exception as e:
                logger.warning("Failed to process critique finding: %s", e)
                continue

        return tasks

    def _process_critique_finding(
        self, finding: dict, result: IntrospectionResult
    ) -> GeneratedTask | None:
        """Process a single critique finding into a task.

        Args:
            finding: The critique finding dict
            result: The parent IntrospectionResult

        Returns:
            GeneratedTask or None if finding should be skipped
        """
        finding_type = self._extract_finding_field(finding, "type", "")

        # Skip non-actionable types
        if finding_type in ("strength", "analysis_error"):
            return None

        if finding_type == "pattern":
            pattern = self._extract_finding_field(finding, "pattern", "")
            frequency = self._extract_finding_field(finding, "frequency", "unknown")
            if not pattern:
                return None

            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_pattern_prompt(pattern, frequency),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={
                    "finding_type": finding_type,
                    "pattern": pattern,
                    "frequency": frequency,
                },
                estimated_complexity="medium",
                success_criteria="Pattern frequency decreases or is resolved",
            )

        elif finding_type == "weakness":
            area = self._extract_finding_field(finding, "area", "unknown")
            description = self._extract_finding_field(finding, "description", "")
            recommendation = self._extract_finding_field(finding, "recommendation", "")
            impact = self._extract_finding_field(finding, "impact", "")

            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_weakness_prompt(
                    area, description, recommendation, impact
                ),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={
                    "finding_type": finding_type,
                    "area": area,
                    "description": description,
                },
                estimated_complexity="medium" if impact != "high" else "large",
                success_criteria=recommendation or f"Weakness in {area} is addressed",
            )

        return None

    def _generate_from_coverage(
        self, result: IntrospectionResult
    ) -> list[GeneratedTask]:
        """Generate tasks from coverage introspection findings.

        Handles finding types:
        - total_coverage: Overall coverage percentage
        - low_coverage_files: Files with < 50% coverage
        - todo_count: TODO/FIXME comments found
        - untested_modules: Modules without test files

        Args:
            result: CoverageMode introspection result

        Returns:
            List of generated tasks
        """
        tasks: list[GeneratedTask] = []

        for finding in result.findings:
            try:
                task = self._process_coverage_finding(finding, result)
                if task:
                    tasks.append(task)
            except Exception as e:
                logger.warning("Failed to process coverage finding: %s", e)
                continue

        return tasks

    def _process_coverage_finding(
        self, finding: dict, result: IntrospectionResult
    ) -> GeneratedTask | None:
        """Process a single coverage finding into a task.

        Args:
            finding: The coverage finding dict
            result: The parent IntrospectionResult

        Returns:
            GeneratedTask or None if finding should be skipped
        """
        finding_type = self._extract_finding_field(finding, "type", "")
        severity = self._infer_severity(finding)

        # Skip info-level findings and skip/error types
        if severity == "info" or finding_type in (
            "coverage_skipped",
            "coverage_error",
            "coverage_timeout",
            "coverage_parse_error",
        ):
            return None

        if finding_type == "total_coverage":
            percent = self._extract_finding_field(finding, "percent", 100.0)
            if percent < 70.0:
                return GeneratedTask(
                    task_id=self._make_task_id(),
                    source="introspection",
                    prompt=self._format_coverage_prompt(percent),
                    priority=self._calculate_priority(finding, result.mode),
                    introspection_mode=result.mode,
                    context={"finding_type": finding_type, "percent": percent},
                    estimated_complexity="medium",
                    success_criteria="Total test coverage reaches at least 70%",
                )

        elif finding_type == "low_coverage_files":
            files = self._extract_finding_field(finding, "files", [])
            if files:
                # Take top 3 lowest coverage files (defensive access for malformed findings)
                target_files = [f.get("file", "unknown") for f in files[:3] if f.get("file")]
                return GeneratedTask(
                    task_id=self._make_task_id(),
                    source="introspection",
                    prompt=self._format_low_coverage_files_prompt(files[:3]),
                    priority=self._calculate_priority(finding, result.mode),
                    introspection_mode=result.mode,
                    context={"finding_type": finding_type, "file_count": len(files)},
                    estimated_complexity="medium",
                    target_files=target_files,
                    success_criteria="Coverage for listed files exceeds 50%",
                )

        elif finding_type == "todo_count":
            total = self._extract_finding_field(finding, "total", 0)
            todos = self._extract_finding_field(finding, "todos", [])
            breakdown = self._extract_finding_field(finding, "breakdown", {})

            if total >= 10:  # Only create task for significant TODO count
                return GeneratedTask(
                    task_id=self._make_task_id(),
                    source="introspection",
                    prompt=self._format_todo_prompt(total, breakdown, todos[:5]),
                    priority=self._calculate_priority(finding, result.mode),
                    introspection_mode=result.mode,
                    context={"finding_type": finding_type, "total": total},
                    estimated_complexity="small",
                    success_criteria=f"Reduce TODO count from {total} to below 10",
                )

        elif finding_type == "untested_modules":
            modules = self._extract_finding_field(finding, "modules", [])
            if modules and len(modules) > 3:
                return GeneratedTask(
                    task_id=self._make_task_id(),
                    source="introspection",
                    prompt=self._format_untested_modules_prompt(modules[:5]),
                    priority=self._calculate_priority(finding, result.mode),
                    introspection_mode=result.mode,
                    context={
                        "finding_type": finding_type,
                        "module_count": len(modules),
                    },
                    estimated_complexity="medium",
                    success_criteria="Add tests for at least 2 of the listed modules",
                )

        return None

    def _generate_from_comparative(
        self, result: IntrospectionResult
    ) -> list[GeneratedTask]:
        """Generate tasks from comparative introspection findings.

        Handles finding types:
        - size_pattern: Winner patches tend to be smaller
        - plan_pattern: Winner plans tend to be simpler
        - failure_stage_pattern: Common failure stage identified

        Args:
            result: ComparativeMode introspection result

        Returns:
            List of generated tasks
        """
        tasks: list[GeneratedTask] = []

        for finding in result.findings:
            try:
                task = self._process_comparative_finding(finding, result)
                if task:
                    tasks.append(task)
            except Exception as e:
                logger.warning("Failed to process comparative finding: %s", e)
                continue

        return tasks

    def _process_comparative_finding(
        self, finding: dict, result: IntrospectionResult
    ) -> GeneratedTask | None:
        """Process a single comparative finding into a task.

        Args:
            finding: The comparative finding dict
            result: The parent IntrospectionResult

        Returns:
            GeneratedTask or None if finding should be skipped
        """
        finding_type = self._extract_finding_field(finding, "type", "")
        severity = self._infer_severity(finding)

        # Skip info-only findings (group_stats, patch_comparison details)
        if finding_type in ("group_stats", "patch_comparison", "no_comparable_groups"):
            return None

        detail = self._extract_finding_field(finding, "detail", "")

        if finding_type == "size_pattern":
            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_size_pattern_prompt(detail),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={"finding_type": finding_type},
                estimated_complexity="small",
                success_criteria="Future patches average fewer lines than current",
            )

        elif finding_type == "plan_pattern":
            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_plan_pattern_prompt(detail),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={"finding_type": finding_type},
                estimated_complexity="small",
                success_criteria="Future plans average fewer steps than current",
            )

        elif finding_type == "failure_stage_pattern":
            return GeneratedTask(
                task_id=self._make_task_id(),
                source="introspection",
                prompt=self._format_failure_stage_pattern_prompt(detail),
                priority=self._calculate_priority(finding, result.mode),
                introspection_mode=result.mode,
                context={"finding_type": finding_type, "detail": detail},
                estimated_complexity="medium",
                success_criteria="Dominant failure stage percentage decreases",
            )

        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Prompt Templates
    # ─────────────────────────────────────────────────────────────────────────

    def _format_success_rate_prompt(self, value: float) -> str:
        """Format prompt for low success rate finding."""
        return f"""# Practice: Improve Success Rate

Your current success rate is {value * 100:.0f}%, below the healthy threshold of 70%.

## What
Practice with smaller, well-defined tasks to build confidence and establish patterns.

## Focus Areas
- Break large tasks into smaller steps
- Verify each step before proceeding
- Use existing patterns that have worked before

## Success Criteria
The next introspection cycle should show success rate improvement toward 70%."""

    def _format_error_rate_prompt(self, value: float) -> str:
        """Format prompt for high error rate finding."""
        return f"""# Investigation: High Error Rate

Your current error rate is {value * 100:.0f}%, above the critical threshold of 10%.

## What
Investigate the root causes of errors and implement fixes.

## Analysis Steps
1. Review recent error messages in episode records
2. Identify common error patterns
3. Check for infrastructure issues (API timeouts, git problems)
4. Validate task prompt formatting

## Success Criteria
Error rate should drop below 10% in the next cycle."""

    def _format_retry_rate_prompt(self, value: float) -> str:
        """Format prompt for high retry rate finding."""
        return f"""# Improvement: High Retry Rate

Your current retry rate is {value * 100:.0f}%, above the threshold of 30%.

## What
Improve initial plan quality to reduce the need for retries.

## Strategies
- Include more validation steps in plans
- Consider edge cases upfront
- Test assumptions before implementing

## Success Criteria
Retry rate should drop below 30% in the next cycle."""

    def _format_failure_stage_prompt(self, detail: str) -> str:
        """Format prompt for failure concentration finding."""
        return f"""# Focus Area: Failure Stage Concentration

{detail}

## What
Investigate why failures cluster at this stage and implement improvements.

## Analysis Steps
1. Review episode records for this failure stage
2. Identify common patterns in failures
3. Adjust approach for this stage

## Success Criteria
Failure concentration at this stage should decrease."""

    def _format_pattern_prompt(self, pattern: str, frequency: str) -> str:
        """Format prompt for identified pattern finding."""
        return f"""# Address Pattern: {pattern}

A {frequency} pattern has been identified in your recent work.

## Pattern
{pattern}

## What
Analyze this pattern and determine if it indicates an issue to fix or a successful approach to reinforce.

## Success Criteria
If negative: Pattern frequency should decrease.
If positive: Pattern should be documented and reused."""

    def _format_weakness_prompt(
        self, area: str, description: str, recommendation: str, impact: str
    ) -> str:
        """Format prompt for weakness finding."""
        impact_note = f" (Impact: {impact})" if impact else ""
        rec_section = f"\n\n## Recommendation\n{recommendation}" if recommendation else ""

        return f"""# Improve Weakness: {area}{impact_note}

## Description
{description}
{rec_section}

## Success Criteria
This weakness should be addressed or mitigated."""

    def _format_coverage_prompt(self, percent: float) -> str:
        """Format prompt for low total coverage finding."""
        return f"""# Improve Test Coverage

Current test coverage is {percent:.0f}%, below the target of 70%.

## What
Add tests to improve overall coverage.

## Focus
- Prioritize untested code paths
- Add tests for error handling
- Cover edge cases

## Success Criteria
Total test coverage reaches at least 70%."""

    def _format_low_coverage_files_prompt(
        self, files: list[dict[str, Any]]
    ) -> str:
        """Format prompt for low coverage files finding."""
        file_list = "\n".join(
            f"- {f.get('file', 'unknown')}: {f.get('percent', 0):.0f}% coverage"
            for f in files
            if f.get("file")
        )

        return f"""# Improve File Coverage

The following files have low test coverage (< 50%):

{file_list}

## What
Add tests specifically targeting these files.

## Success Criteria
Coverage for these files exceeds 50%."""

    def _format_todo_prompt(
        self, total: int, breakdown: dict, todos: list[dict]
    ) -> str:
        """Format prompt for high TODO count finding."""
        breakdown_str = ", ".join(f"{k}: {v}" for k, v in breakdown.items() if v > 0)
        sample_list = "\n".join(
            f"- {t.get('file', 'unknown')}:{t.get('line', '?')} - {t.get('text', '')}"
            for t in todos[:5]
            if t.get("file")
        )

        return f"""# Address TODOs

Found {total} TODO/FIXME/XXX/HACK comments ({breakdown_str}).

## Sample Items
{sample_list}

## What
Review and address the most critical TODOs.

## Success Criteria
Reduce TODO count from {total} to below 10."""

    def _format_untested_modules_prompt(self, modules: list[str]) -> str:
        """Format prompt for untested modules finding."""
        module_list = "\n".join(f"- {m}" for m in modules)

        return f"""# Add Missing Tests

The following modules have no corresponding test files:

{module_list}

## What
Create test files for these modules with basic coverage.

## Success Criteria
Add tests for at least 2 of the listed modules."""

    def _format_size_pattern_prompt(self, detail: str) -> str:
        """Format prompt for size pattern finding."""
        return f"""# Apply Learning: Patch Size

{detail}

## What
Focus on creating smaller, more focused patches.

## Strategies
- Make minimal changes to achieve the goal
- Split large changes into multiple smaller patches
- Avoid unnecessary refactoring in the same patch

## Success Criteria
Future patches should average fewer lines."""

    def _format_plan_pattern_prompt(self, detail: str) -> str:
        """Format prompt for plan pattern finding."""
        return f"""# Apply Learning: Plan Simplicity

{detail}

## What
Focus on creating simpler, more direct plans.

## Strategies
- Reduce plan steps to essential actions
- Avoid over-engineering solutions
- Test simple approaches first

## Success Criteria
Future plans should average fewer steps."""

    def _format_failure_stage_pattern_prompt(self, detail: str) -> str:
        """Format prompt for failure stage pattern finding."""
        return f"""# Address Failure Stage

{detail}

## What
Investigate and improve the failing stage.

## Analysis
1. Review why this stage fails most often
2. Identify preventive measures
3. Add validation before reaching this stage

## Success Criteria
Dominant failure stage percentage should decrease."""

    # ─────────────────────────────────────────────────────────────────────────
    # Evolution / Merge Failure Tasks
    # ─────────────────────────────────────────────────────────────────────────

    def generate_merge_failure_task(
        self,
        failure_count: int,
        last_error: str | None = None,
    ) -> GeneratedTask:
        """Generate a task to investigate merge failures.

        Called when merge operations fail consecutively, indicating
        a systemic issue that needs investigation.

        Args:
            failure_count: Number of consecutive merge failures
            last_error: The error message from the last failed merge

        Returns:
            GeneratedTask for investigating and fixing the merge pipeline
        """
        prompt = f"""# Investigate and Fix Recurring Merge Failures

## Context
- {failure_count} consecutive merge operations have failed
- Last error: {last_error or 'Unknown'}

## Analysis Needed
1. Check merge configuration (backend, method, parameters)
2. Verify LoRA adapter compatibility
3. Check disk space and permissions for output directory
4. Review validation criteria (may be too strict)
5. Examine recent changes to evolution system

## Files to Review
- homunculus/evolution/merge.py
- homunculus/evolution/validation.py
- homunculus/config.py

## Success Criteria
Merge pipeline executes successfully and produces a validated merged model."""

        return GeneratedTask(
            task_id=f"merge-fix-{uuid.uuid4().hex[:8]}",
            source="introspection",
            prompt=prompt,
            priority=0.9,  # High priority - blocks evolution
            introspection_mode="merge_failure",
            estimated_complexity="medium",
            target_files=[
                "homunculus/evolution/merge.py",
                "homunculus/evolution/validation.py",
                "homunculus/config.py",
            ],
            success_criteria="Merge pipeline executes successfully",
        )
