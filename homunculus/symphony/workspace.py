from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .models import IssueRecord, SymphonyConfig, WorkspaceRecord


class WorkspaceError(RuntimeError):
    pass


_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def sanitize_workspace_key(identifier: str) -> str:
    return _SAFE_KEY_RE.sub("_", identifier).strip("._-") or "issue"


def branch_name_for_issue(issue: IssueRecord, config: SymphonyConfig) -> str:
    slug = _SLUG_RE.sub("-", issue.title.lower()).strip("-")[:48] or "work"
    return f"{config.homunculus.branch_prefix}{sanitize_workspace_key(issue.identifier)}-{slug}"


class WorkspaceManager:
    def __init__(self, config: SymphonyConfig) -> None:
        self.config = config

    def ensure_workspace(self, issue: IssueRecord) -> WorkspaceRecord:
        source = self.config.homunculus.source_workspace
        workspace_key = sanitize_workspace_key(issue.identifier)
        workspace_path = self.config.workspace.root / workspace_key
        branch_name = issue.branch_name or branch_name_for_issue(issue, self.config)

        self._require_git_repo(source)
        self._require_clean(source)
        self.config.workspace.root.mkdir(parents=True, exist_ok=True)

        created_now = False
        if not workspace_path.exists():
            self._run_git(
                source,
                [
                    "worktree",
                    "add",
                    "-B",
                    branch_name,
                    str(workspace_path),
                    self.config.homunculus.base_branch,
                ],
                check=True,
            )
            created_now = True
        else:
            self._require_git_repo(workspace_path)
        return WorkspaceRecord(
            path=workspace_path.resolve(),
            workspace_key=workspace_key,
            branch_name=branch_name,
            created_now=created_now,
        )

    def remove_workspace(self, workspace: WorkspaceRecord) -> None:
        source = self.config.homunculus.source_workspace
        if workspace.path.exists():
            self._run_git(source, ["worktree", "remove", "--force", str(workspace.path)], check=False)
            shutil.rmtree(workspace.path, ignore_errors=True)
        self._run_git(source, ["worktree", "prune"], check=False)

    def _require_git_repo(self, path: Path) -> None:
        if not (path / ".git").exists() and not (path / ".git").is_file():
            raise WorkspaceError(f"not a git workspace: {path}")
        if shutil.which("git") is None:
            raise WorkspaceError("git is required for Symphony workspaces")

    def _require_clean(self, path: Path) -> None:
        status = self._run_git(path, ["status", "--porcelain"], check=True).stdout.strip()
        if status:
            raise WorkspaceError(f"workspace must be clean before Symphony dispatch: {path}")

    def _run_git(
        self, cwd: Path, args: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if check and result.returncode != 0:
            raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr}")
        return result
