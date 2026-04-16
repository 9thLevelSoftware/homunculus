"""Coverage introspection mode for code quality analysis."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import IntrospectionResult, utc_now

if TYPE_CHECKING:
    from .base import IntrospectionContext


class CoverageMode:
    """Introspection mode that analyzes code quality signals.

    Combines:
    - Test coverage via pytest-cov
    - TODO/FIXME/XXX/HACK comment scanning
    - Test gap analysis (source modules without test files)
    """

    @property
    def name(self) -> str:
        """Mode identifier."""
        return "coverage"

    def run(self, context: "IntrospectionContext") -> IntrospectionResult:
        """Execute coverage introspection and return findings."""
        findings: list[dict[str, Any]] = []
        metrics: dict[str, float] = {}
        recommendations: list[str] = []

        # Run coverage analysis
        coverage_findings, coverage_metrics = self._run_coverage(context)
        findings.extend(coverage_findings)
        metrics.update(coverage_metrics)

        # Scan for TODO/FIXME comments
        todo_findings, todo_metrics = self._scan_todos(context)
        findings.extend(todo_findings)
        metrics.update(todo_metrics)

        # Find test gaps
        gap_findings, gap_recommendations = self._find_test_gaps(context)
        findings.extend(gap_findings)
        recommendations.extend(gap_recommendations)

        # Format summary
        summary = self._format_summary(metrics, findings)

        return IntrospectionResult(
            mode=self.name,
            timestamp=utc_now(),
            findings=findings,
            summary=summary,
            metrics=metrics,
            recommendations=recommendations,
        )

    def _run_coverage(
        self, context: "IntrospectionContext"
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """Run pytest-cov and analyze coverage.

        Uses two-step approach:
        1. Run pytest with coverage (generates .coverage file)
        2. Convert to JSON with coverage json command

        Returns:
            Tuple of (findings list, metrics dict)
        """
        findings: list[dict[str, Any]] = []
        metrics: dict[str, float] = {}

        # Check if pytest and coverage are available
        try:
            subprocess.run(
                [sys.executable, "-c", "import pytest; import coverage"],
                capture_output=True,
                timeout=10,
                check=True,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            findings.append({
                "type": "coverage_skipped",
                "reason": "pytest or coverage not installed",
                "severity": "info",
            })
            return findings, metrics

        # Create temp file for JSON output
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json_path = Path(tmp.name)

        try:
            # Get workspace root from config
            workspace_root = self._get_workspace_root(context)

            # Derive source directory name from config or workspace
            source_dir_name = self._get_source_dir_name(context)

            # Step 1: Run pytest with coverage
            pytest_result = subprocess.run(
                [
                    sys.executable, "-m", "pytest",
                    f"--cov={source_dir_name}",
                    "--cov-report=",  # Suppress default output, just write .coverage
                    "-q",
                ],
                capture_output=True,
                timeout=120,
                cwd=workspace_root,
            )

            # Step 2: Convert .coverage to JSON
            coverage_result = subprocess.run(
                [sys.executable, "-m", "coverage", "json", "-o", str(json_path)],
                capture_output=True,
                timeout=30,
                cwd=workspace_root,
            )

            if coverage_result.returncode != 0:
                findings.append({
                    "type": "coverage_error",
                    "reason": "Failed to generate coverage JSON",
                    "stderr": coverage_result.stderr.decode("utf-8", errors="replace")[:500],
                    "severity": "warning",
                })
                return findings, metrics

            # Parse JSON report
            if json_path.exists():
                with open(json_path) as f:
                    report = json.load(f)

                total_coverage = report.get("totals", {}).get("percent_covered", 0.0)
                metrics["total_coverage"] = total_coverage

                # Determine severity
                severity = "info" if total_coverage >= 70.0 else "warning"
                findings.append({
                    "type": "total_coverage",
                    "percent": total_coverage,
                    "severity": severity,
                })

                # Find low-coverage files (< 50%)
                low_coverage_files: list[dict[str, Any]] = []
                files_data = report.get("files", {})
                for filepath, file_report in files_data.items():
                    file_coverage = file_report.get("summary", {}).get(
                        "percent_covered", 100.0
                    )
                    if file_coverage < 50.0:
                        low_coverage_files.append({
                            "file": filepath,
                            "percent": file_coverage,
                        })

                if low_coverage_files:
                    # Sort by coverage ascending
                    low_coverage_files.sort(key=lambda x: x["percent"])
                    findings.append({
                        "type": "low_coverage_files",
                        "files": low_coverage_files,
                        "count": len(low_coverage_files),
                        "severity": "warning",
                    })
                    metrics["low_coverage_file_count"] = float(len(low_coverage_files))

        except subprocess.TimeoutExpired:
            findings.append({
                "type": "coverage_timeout",
                "reason": "Coverage analysis timed out",
                "severity": "warning",
            })
        except json.JSONDecodeError as e:
            findings.append({
                "type": "coverage_parse_error",
                "reason": f"Failed to parse coverage JSON: {e}",
                "severity": "warning",
            })
        except Exception as e:
            findings.append({
                "type": "coverage_error",
                "reason": f"Analysis failed ({type(e).__name__}): {str(e)[:200]}",
                "severity": "warning",
            })
        finally:
            # Clean up temp file
            try:
                json_path.unlink(missing_ok=True)
            except Exception:
                pass

        return findings, metrics

    def _scan_todos(
        self, context: "IntrospectionContext"
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """Scan source files for TODO/FIXME/XXX/HACK comments.

        Returns:
            Tuple of (findings list, metrics dict)
        """
        findings: list[dict[str, Any]] = []
        metrics: dict[str, float] = {}

        workspace_root = self._get_workspace_root(context)
        source_dir_name = self._get_source_dir_name(context)
        source_dir = workspace_root / source_dir_name

        if not source_dir.exists():
            return findings, metrics

        # Pattern to match TODO/FIXME/XXX/HACK comments
        todo_pattern = re.compile(
            r"#\s*(TODO|FIXME|XXX|HACK)[\s:]+(.+)", re.IGNORECASE
        )

        todos: list[dict[str, Any]] = []
        todo_count = 0
        fixme_count = 0
        xxx_count = 0
        hack_count = 0

        for py_file in source_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for line_num, line in enumerate(content.splitlines(), start=1):
                    match = todo_pattern.search(line)
                    if match:
                        todo_type = match.group(1).upper()
                        text = match.group(2).strip()[:100]  # Truncate to 100 chars

                        # Count by type
                        if todo_type == "TODO":
                            todo_count += 1
                        elif todo_type == "FIXME":
                            fixme_count += 1
                        elif todo_type == "XXX":
                            xxx_count += 1
                        elif todo_type == "HACK":
                            hack_count += 1

                        try:
                            relative_path = py_file.relative_to(workspace_root)
                        except ValueError:
                            relative_path = py_file

                        todos.append({
                            "file": str(relative_path),
                            "line": line_num,
                            "type": todo_type,
                            "text": text,
                        })
            except Exception:
                # Skip files that can't be read
                continue

        total_count = todo_count + fixme_count + xxx_count + hack_count
        metrics["todo_count"] = float(todo_count)
        metrics["fixme_count"] = float(fixme_count)

        if todos:
            severity = "info" if total_count < 10 else "warning"
            findings.append({
                "type": "todo_count",
                "total": total_count,
                "todos": todos,
                "breakdown": {
                    "TODO": todo_count,
                    "FIXME": fixme_count,
                    "XXX": xxx_count,
                    "HACK": hack_count,
                },
                "severity": severity,
            })

        return findings, metrics

    def _find_test_gaps(
        self, context: "IntrospectionContext"
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Find source modules without corresponding test files.

        Returns:
            Tuple of (findings list, recommendations list)
        """
        findings: list[dict[str, Any]] = []
        recommendations: list[str] = []

        workspace_root = self._get_workspace_root(context)
        source_dir = workspace_root / self._get_source_dir_name(context)
        tests_dir = workspace_root / "tests"

        if not source_dir.exists():
            return findings, recommendations

        # Get source modules (excluding __init__.py, __main__.py, and _* files)
        source_modules: set[str] = set()
        for py_file in source_dir.rglob("*.py"):
            if py_file.name.startswith("_"):
                continue
            # Extract module name (without .py extension)
            module_name = py_file.stem
            source_modules.add(module_name)

        # Get tested modules from test files
        tested_modules: set[str] = set()
        if tests_dir.exists():
            for test_file in tests_dir.glob("test_*.py"):
                # Extract module name by removing "test_" prefix
                if test_file.name.startswith("test_"):
                    module_name = test_file.stem[5:]  # Remove "test_" prefix
                    tested_modules.add(module_name)

        # Find untested modules
        untested_modules = sorted(source_modules - tested_modules)

        if untested_modules:
            severity = "warning" if len(untested_modules) > 3 else "info"
            findings.append({
                "type": "untested_modules",
                "modules": untested_modules,
                "count": len(untested_modules),
                "severity": severity,
            })

            if len(untested_modules) > 5:
                recommendations.append(
                    f"Consider adding tests for {len(untested_modules)} untested modules. "
                    f"Priority candidates: {', '.join(untested_modules[:5])}"
                )

        return findings, recommendations

    def _format_summary(
        self, metrics: dict[str, float], findings: list[dict[str, Any]]
    ) -> str:
        """Format a human-readable summary of findings."""
        parts: list[str] = []

        # Coverage percentage
        if "total_coverage" in metrics:
            parts.append(f"{metrics['total_coverage']:.0f}% coverage")

        # TODO count
        total_todos = int(metrics.get("todo_count", 0) + metrics.get("fixme_count", 0))
        if total_todos > 0:
            parts.append(f"{total_todos} TODOs")

        if parts:
            return f"Code quality: {', '.join(parts)}"
        return "Code quality: no data collected"

    def _get_workspace_root(self, context: "IntrospectionContext") -> Path:
        """Get the workspace root directory from context.

        For self-targeting introspection, this returns the homunculus project root.
        """
        # Try to get from config workspaces
        if hasattr(context.config, "workspaces"):
            workspaces = context.config.workspaces
            # Look for "self" workspace or use first available
            if "self" in workspaces:
                return Path(workspaces["self"].path)
            elif workspaces:
                first_ws = next(iter(workspaces.values()))
                return Path(first_ws.path)

        # Fallback: use current working directory
        return Path.cwd()

    def _get_source_dir_name(self, context: "IntrospectionContext") -> str:
        """Get the source directory name from config.

        Derives from paths.root.name if available, otherwise defaults to 'homunculus'.
        """
        if hasattr(context.config, "paths") and context.config.paths is not None:
            return context.config.paths.root.name
        return "homunculus"
