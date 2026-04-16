from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess

from ..config import WorkspaceSettings
from ..models import TaskExecutionResult, VerificationResult


class WorkspacePreflightError(RuntimeError):
    pass


@dataclass
class WorkspaceSnapshot:
    path: Path
    git_available: bool
    head: str | None
    status: str
    clean: bool


class TaskRunner:
    def __init__(self, runtime_dir: Path | None = None, git_timeout_seconds: int = 30) -> None:
        self.runtime_dir = Path(runtime_dir).resolve() if runtime_dir else Path("runtime").resolve()
        self.worktrees_dir = self.runtime_dir / "worktrees"
        self.git_timeout_seconds = git_timeout_seconds

    def open_workspace(self, workspace: WorkspaceSettings) -> Path:
        path = workspace.path
        if path.exists():
            return path
        if not workspace.repo_url:
            raise FileNotFoundError(f"Workspace does not exist: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", workspace.repo_url, str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.git_timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr}")
        if workspace.branch:
            self._run_git(path, ["checkout", workspace.branch], check=True)
        return path

    def snapshot(self, workspace_path: Path) -> WorkspaceSnapshot:
        git_available = shutil.which("git") is not None and (workspace_path / ".git").exists()
        head = None
        status = ""
        clean = False
        if git_available:
            head = self._run_git(workspace_path, ["rev-parse", "HEAD"], check=True).stdout.strip()
            status = self._run_git(workspace_path, ["status", "--porcelain"], check=True).stdout
            clean = status.strip() == ""
        return WorkspaceSnapshot(path=workspace_path, git_available=git_available, head=head, status=status, clean=clean)

    def require_clean_workspace(self, workspace: WorkspaceSettings) -> WorkspaceSnapshot:
        workspace_path = self.open_workspace(workspace)
        snapshot = self.snapshot(workspace_path)
        if not snapshot.git_available:
            raise WorkspacePreflightError("Workspace must be a git repository.")
        if not snapshot.clean:
            raise WorkspacePreflightError("Workspace must be clean before an episode can run.")
        return snapshot

    def apply_patch(self, workspace_path: Path, patch: str) -> bool:
        if not patch.strip():
            return False
        if not ((workspace_path / ".git").exists() and shutil.which("git")):
            raise RuntimeError("Patch application requires a git workspace.")
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=workspace_path,
            input=patch,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.git_timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git apply failed: {result.stderr}")
        return True

    def run_verification(self, workspace_path: Path, commands) -> list[VerificationResult]:
        results: list[VerificationResult] = []
        for command in commands:
            completed = subprocess.run(
                command.command,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                shell=True,
                check=False,
                timeout=command.timeout_seconds,
            )
            results.append(
                VerificationResult(
                    name=command.name,
                    command=command.command,
                    kind=command.kind,
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    passed=completed.returncode == 0,
                )
            )
        return results

    def execute_patch(self, workspace: WorkspaceSettings, episode_id: str, patch: str | None) -> TaskExecutionResult:
        snapshot = self.require_clean_workspace(workspace)
        worktree_path = self._create_worktree(snapshot, episode_id)
        try:
            applied = False
            if patch:
                applied = self.apply_patch(worktree_path, patch)
            results = self.run_verification(worktree_path, workspace.verification_commands)
            canonical_patch = self.read_patch(worktree_path)
            diff_payload = canonical_patch if canonical_patch is not None else (patch or "")
            diff_hash = sha256(diff_payload.encode("utf-8")).hexdigest()
            passed = all(item.passed for item in results)
            return TaskExecutionResult(
                workspace_path=str(snapshot.path),
                diff_hash=diff_hash,
                applied=applied,
                reverted=not passed,
                verification_results=results,
                canonical_patch=canonical_patch,
            )
        finally:
            self._remove_worktree(snapshot.path, worktree_path)

    def apply_episode_patch(self, workspace: WorkspaceSettings, patch: str) -> TaskExecutionResult:
        snapshot = self.require_clean_workspace(workspace)
        applied = False
        try:
            applied = self.apply_patch(snapshot.path, patch)
            results = self.run_verification(snapshot.path, workspace.verification_commands)
            canonical_patch = self.read_patch(snapshot.path)
            diff_hash = sha256((canonical_patch or patch).encode("utf-8")).hexdigest()
            passed = all(item.passed for item in results)
            if not passed:
                raise RuntimeError("Episode patch failed verification and was reverted.")
            return TaskExecutionResult(
                workspace_path=str(snapshot.path),
                diff_hash=diff_hash,
                applied=applied,
                reverted=False,
                verification_results=results,
                canonical_patch=canonical_patch,
            )
        except Exception:
            if applied:
                self.revert(snapshot)
            raise

    def read_patch(self, workspace_path: Path) -> str:
        return self._run_git(workspace_path, ["diff", "--no-ext-diff", "--binary"], check=True).stdout

    def revert(self, snapshot: WorkspaceSnapshot) -> None:
        if not snapshot.git_available or not snapshot.head:
            return
        self._run_git(snapshot.path, ["reset", "--hard", snapshot.head], check=True)
        self._run_git(snapshot.path, ["clean", "-fd"], check=True)

    def _create_worktree(self, snapshot: WorkspaceSnapshot, episode_id: str) -> Path:
        if not snapshot.head:
            raise WorkspacePreflightError("Workspace HEAD is required for linked worktree execution.")
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = self.worktrees_dir / episode_id
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
        self._run_git(snapshot.path, ["worktree", "add", "--detach", str(worktree_path), snapshot.head], check=True)
        return worktree_path

    def _remove_worktree(self, source_path: Path, worktree_path: Path) -> None:
        if not worktree_path.exists():
            return
        self._run_git(source_path, ["worktree", "remove", "--force", str(worktree_path)], check=False)
        self._run_git(source_path, ["worktree", "prune"], check=False)
        shutil.rmtree(worktree_path, ignore_errors=True)

    def _run_git(self, workspace_path: Path, args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.git_timeout_seconds,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
        return result
