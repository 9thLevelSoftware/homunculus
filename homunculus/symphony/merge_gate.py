from __future__ import annotations

import subprocess
from pathlib import Path

from .models import MergeGateResult, SymphonyConfig, WorkspaceRecord


class MergeGateError(RuntimeError):
    pass


class MergeGate:
    def __init__(self, config: SymphonyConfig) -> None:
        self.config = config

    def run_gates(self, workspace: WorkspaceRecord) -> list[MergeGateResult]:
        results: list[MergeGateResult] = []
        for command in self.config.homunculus.merge_gates:
            completed = subprocess.run(
                command,
                cwd=workspace.path,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
            result = MergeGateResult(
                name=command.split()[0] if command.split() else "gate",
                command=command,
                passed=completed.returncode == 0,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            results.append(result)
            if not result.passed:
                break
        return results

    def merge_branch(self, workspace: WorkspaceRecord) -> str:
        source = self.config.homunculus.source_workspace
        self._require_clean(source)
        self._require_clean(workspace.path)
        current = self._git(source, ["branch", "--show-current"], check=True).stdout.strip()
        if current != self.config.homunculus.base_branch:
            raise MergeGateError(
                f"source workspace must be on {self.config.homunculus.base_branch}; got {current}"
            )
        self._git(source, ["merge", "--ff-only", workspace.branch_name], check=True)
        return self._git(source, ["rev-parse", "HEAD"], check=True).stdout.strip()

    def _require_clean(self, path: Path) -> None:
        status = self._git(path, ["status", "--porcelain"], check=True).stdout.strip()
        if status:
            raise MergeGateError(f"source workspace is not clean: {path}")

    def _git(
        self, cwd: Path, args: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if check and completed.returncode != 0:
            raise MergeGateError(f"git {' '.join(args)} failed: {completed.stderr}")
        return completed
