from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True)
class HarnessCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class HarnessReport:
    root: str
    strict: bool
    checks: list[HarnessCheck]

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "strict": self.strict,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


REQUIRED_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "WORKFLOW.md",
    "docs/index.md",
    "docs/harness-engineering.md",
    "docs/architecture.md",
    "docs/operator-guide.md",
    "docs/setup-and-configuration.md",
    "docs/quality-score.md",
    "docs/symphony-autonomy.md",
    "docs/vm-runbook.md",
    "homunculus.example.toml",
    ".github/workflows/harness.yml",
]

INDEXED_DOCS = [
    "harness-engineering.md",
    "architecture.md",
    "operator-guide.md",
    "setup-and-configuration.md",
    "quality-score.md",
    "symphony-autonomy.md",
    "vm-runbook.md",
]

STALE_GUIDANCE = [
    "require_human_approval",
    "human approval required",
    "promotion is intentionally manual",
    "accepted patches stay as artifacts until you explicitly apply",
    "auto-applying accepted patches",
    "auto-promoting trained candidates",
    "does not auto-apply",
]

TEXT_SURFACES = [
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/index.md",
    "docs/harness-engineering.md",
    "docs/architecture.md",
    "docs/operator-guide.md",
    "docs/setup-and-configuration.md",
    "docs/symphony-autonomy.md",
    "docs/vm-runbook.md",
    "docs/quality-score.md",
]


def run_harness_check(root: str | Path = ".", *, strict: bool = False) -> HarnessReport:
    root_path = Path(root).resolve()
    checks = [
        _check_required_files(root_path),
        _check_agents_map(root_path),
        _check_doc_index(root_path),
        _check_autonomous_defaults(root_path),
        _check_workflow_contract(root_path),
        _check_ci_workflow(root_path),
    ]
    stale_check = (
        _check_no_stale_guidance(root_path)
        if strict
        else HarnessCheck(
            "stale-guidance",
            True,
            "skipped without --strict",
        )
    )
    checks.insert(3, stale_check)
    return HarnessReport(root=str(root_path), strict=strict, checks=checks)


def format_harness_report(report: HarnessReport) -> str:
    lines = [
        f"harness root: {report.root}",
        f"strict: {str(report.strict).lower()}",
        f"ok: {str(report.ok).lower()}",
        "",
    ]
    width = max(len(check.name) for check in report.checks)
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"{status}  {check.name.ljust(width)}  {check.detail}")
    return "\n".join(lines)


def _check_required_files(root: Path) -> HarnessCheck:
    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]
    if missing:
        return HarnessCheck(
            name="required-files",
            passed=False,
            detail=f"missing: {', '.join(missing)}",
        )
    return HarnessCheck(
        name="required-files",
        passed=True,
        detail=f"{len(REQUIRED_FILES)} required files present",
    )


def _check_agents_map(root: Path) -> HarnessCheck:
    path = root / "AGENTS.md"
    if not path.exists():
        return HarnessCheck("agents-map", False, "AGENTS.md is missing")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    required = [
        "docs/index.md",
        "docs/harness-engineering.md",
        "docs/architecture.md",
        "python -m homunculus.cli harness-check --strict",
    ]
    missing = [item for item in required if item not in text]
    if len(lines) > 120:
        missing.append(f"line budget exceeded: {len(lines)} > 120")
    if missing:
        return HarnessCheck("agents-map", False, "; ".join(missing))
    return HarnessCheck(
        "agents-map",
        True,
        f"AGENTS.md is {len(lines)} lines and links to current docs",
    )


def _check_doc_index(root: Path) -> HarnessCheck:
    path = root / "docs" / "index.md"
    if not path.exists():
        return HarnessCheck("doc-index", False, "docs/index.md is missing")
    text = path.read_text(encoding="utf-8")
    missing = [name for name in INDEXED_DOCS if name not in text]
    if missing:
        return HarnessCheck("doc-index", False, f"missing links: {', '.join(missing)}")
    return HarnessCheck("doc-index", True, "current docs are indexed")


def _check_no_stale_guidance(root: Path) -> HarnessCheck:
    hits: list[str] = []
    for relative in TEXT_SURFACES:
        path = root / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for phrase in STALE_GUIDANCE:
            if phrase.lower() in text:
                hits.append(f"{relative}: {phrase}")
    if hits:
        return HarnessCheck("stale-guidance", False, "; ".join(hits))
    return HarnessCheck(
        "stale-guidance",
        True,
        "no known stale manual-approval guidance in current docs",
    )


def _check_autonomous_defaults(root: Path) -> HarnessCheck:
    targets = [root / "homunculus.example.toml"]
    local = root / "homunculus.toml"
    if local.exists():
        targets.append(local)

    failures: list[str] = []
    for path in targets:
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            failures.append(f"{path.name}: cannot parse ({exc})")
            continue

        expected = {
            ("daemon", "target_workspace"): "self",
            ("daemon", "auto_commit_on_accept"): True,
            ("evolution", "auto_promote"): True,
            ("evolution", "auto_apply"): True,
        }
        for (section, key), value in expected.items():
            observed = raw.get(section, {}).get(key)
            if observed != value:
                failures.append(
                    f"{path.name}: [{section}].{key} expected {value!r}, got {observed!r}"
                )
    if failures:
        return HarnessCheck("autonomous-defaults", False, "; ".join(failures))
    names = ", ".join(path.name for path in targets)
    return HarnessCheck(
        "autonomous-defaults",
        True,
        f"autonomous defaults explicit in {names}",
    )


def _check_workflow_contract(root: Path) -> HarnessCheck:
    path = root / "WORKFLOW.md"
    if not path.exists():
        return HarnessCheck("workflow-contract", False, "WORKFLOW.md is missing")
    try:
        from .symphony.workflow import load_workflow

        config = load_workflow(path)
    except Exception as exc:
        return HarnessCheck("workflow-contract", False, f"WORKFLOW.md parse failed: {exc}")
    required = [
        config.tracker.kind,
        config.tracker.project_slug,
        config.prompt_template,
        str(config.workspace.root),
        str(config.homunculus.config_path),
    ]
    if not all(required):
        return HarnessCheck("workflow-contract", False, "WORKFLOW.md is missing required fields")
    return HarnessCheck(
        "workflow-contract",
        True,
        "WORKFLOW.md parses and declares tracker/workspace/runner contract",
    )


def _check_ci_workflow(root: Path) -> HarnessCheck:
    path = root / ".github" / "workflows" / "harness.yml"
    if not path.exists():
        return HarnessCheck("ci-workflow", False, ".github/workflows/harness.yml is missing")
    text = path.read_text(encoding="utf-8")
    required = [
        "python -m homunculus.cli harness-check --strict",
        "python -m unittest discover -q",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        return HarnessCheck("ci-workflow", False, f"missing commands: {', '.join(missing)}")
    return HarnessCheck("ci-workflow", True, "CI runs harness check and unit tests")
