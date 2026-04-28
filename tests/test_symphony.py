from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

from homunculus.symphony.merge_gate import MergeGate
from homunculus.symphony.models import AgentResult, IssueRecord
from homunculus.symphony.orchestrator import SymphonyOrchestrator
from homunculus.symphony.status import load_symphony_status
from homunculus.symphony.tracker import LinearTracker
from homunculus.symphony.workflow import WorkflowError, load_workflow, render_prompt
from homunculus.symphony.workspace import WorkspaceManager, branch_name_for_issue, sanitize_workspace_key


def _run_git(cwd: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "git is required")
class SymphonyWorkflowTests(unittest.TestCase):
    def _make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        _run_git(repo, ["init"])
        _run_git(repo, ["config", "user.email", "test@example.com"])
        _run_git(repo, ["config", "user.name", "Test User"])
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        (repo / ".gitignore").write_text("runtime/\n", encoding="utf-8")
        _run_git(repo, ["add", "."])
        _run_git(repo, ["commit", "-m", "init"])
        return repo

    def _workflow_path(self, root: Path, repo: Path, *, gates: str | None = None) -> Path:
        config_path = root / "homunculus.toml"
        config_path.write_text("[placeholder]\n", encoding="utf-8")
        gate_block = gates or '    - python -c "print(\'ok\')"'
        workflow = root / "WORKFLOW.md"
        workflow.write_text(
            f"""---
tracker:
  kind: linear
  api_key: "token"
  project_slug: test-project
  label: symphony
  active_states:
    - Todo
    - In Progress
  terminal_states:
    - Done
workspace:
  root: workspaces
homunculus:
  config_path: {config_path.as_posix()}
  source_workspace: {repo.as_posix()}
  base_branch: master
  runner: homunculus
  auto_merge: true
  merge_gates:
{gate_block}
---
Issue {{{{ issue.identifier }}}}: {{{{ issue.title }}}}
""",
            encoding="utf-8",
        )
        return workflow

    def test_workflow_loads_defaults_and_resolves_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._make_repo(root)
            workflow = self._workflow_path(root, repo)

            config = load_workflow(workflow)

            self.assertEqual(config.tracker.kind, "linear")
            self.assertEqual(config.tracker.project_slug, "test-project")
            self.assertEqual(config.tracker.label, "symphony")
            self.assertEqual(config.workspace.root, (root / "workspaces").resolve())
            self.assertEqual(config.homunculus.source_workspace, repo.resolve())

    def test_render_prompt_fails_on_unknown_variable(self) -> None:
        issue = IssueRecord(id="1", identifier="HOM-1", title="Test")
        with self.assertRaises(WorkflowError):
            render_prompt("{{ issue.missing }}", issue=issue)

    def test_workspace_key_and_branch_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._make_repo(root)
            config = load_workflow(self._workflow_path(root, repo))
            issue = IssueRecord(id="1", identifier="HOM 1", title="Add Symphony core!")

            self.assertEqual(sanitize_workspace_key(issue.identifier), "HOM_1")
            self.assertEqual(
                branch_name_for_issue(issue, config),
                "codex/HOM_1-add-symphony-core",
            )


@unittest.skipUnless(shutil.which("git"), "git is required")
class SymphonyOrchestratorTests(unittest.TestCase):
    def _make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        _run_git(repo, ["init"])
        _run_git(repo, ["config", "user.email", "test@example.com"])
        _run_git(repo, ["config", "user.name", "Test User"])
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        (repo / ".gitignore").write_text("runtime/\n", encoding="utf-8")
        _run_git(repo, ["add", "."])
        _run_git(repo, ["commit", "-m", "init"])
        return repo

    def _workflow_path(self, root: Path, repo: Path, gate_command: str) -> Path:
        config_path = root / "homunculus.toml"
        config_path.write_text("[placeholder]\n", encoding="utf-8")
        workflow = root / "WORKFLOW.md"
        workflow.write_text(
            f"""---
tracker:
  kind: linear
  api_key: "token"
  project_slug: test-project
  active_states:
    - Todo
  terminal_states:
    - Done
workspace:
  root: workspaces
agent:
  max_concurrent_agents: 1
homunculus:
  config_path: {config_path.as_posix()}
  source_workspace: {repo.as_posix()}
  base_branch: master
  auto_merge: true
  merge_gates:
    - {gate_command}
---
Please solve {{{{ issue.identifier }}}}: {{{{ issue.title }}}}
""",
            encoding="utf-8",
        )
        return workflow

    def test_run_once_executes_branch_gates_merges_and_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._make_repo(root)
            gate = 'python -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\'agent.txt\').read_text().strip()==\'done\' else 1)"'
            config = load_workflow(self._workflow_path(root, repo, gate))
            issue = IssueRecord(
                id="issue-1",
                identifier="HOM-1",
                title="Add agent file",
                state="Todo",
                labels=["symphony"],
                created_at="2026-04-28T00:00:00Z",
            )
            tracker = FakeTracker([issue])
            orchestrator = SymphonyOrchestrator(
                config,
                tracker,
                runner=CommittingRunner(),
                workspace_manager=WorkspaceManager(config),
                merge_gate=MergeGate(config),
            )

            result = orchestrator.run_once()

            self.assertEqual(result["executed"], 1)
            self.assertEqual(result["succeeded"], 1)
            self.assertEqual((repo / "agent.txt").read_text(encoding="utf-8"), "done\n")
            state = json.loads(config.state_path.read_text(encoding="utf-8"))
            self.assertIn("issue-1", state["completed"])
            runs = config.runs_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(runs), 1)
            self.assertEqual(json.loads(runs[0])["status"], "succeeded")
            self.assertIn(("issue-1", "Done"), tracker.state_updates)
            status = load_symphony_status(config.runtime_dir)
            self.assertEqual(len(status["recent_runs"]), 1)

    def test_failed_runner_schedules_retry_without_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._make_repo(root)
            config = load_workflow(self._workflow_path(root, repo, 'python -c "print(\'ok\')"'))
            issue = IssueRecord(id="issue-2", identifier="HOM-2", title="Fail", state="Todo")
            orchestrator = SymphonyOrchestrator(
                config,
                FakeTracker([issue]),
                runner=FailingRunner(),
            )

            result = orchestrator.run_once()

            self.assertEqual(result["failed"], 1)
            state = json.loads(config.state_path.read_text(encoding="utf-8"))
            self.assertIn("issue-2", state["retry_attempts"])
            self.assertFalse((repo / "agent.txt").exists())


class SymphonyLinearTrackerTests(unittest.TestCase):
    def test_linear_tracker_normalizes_issues_and_blockers(self) -> None:
        tracker = LinearTracker.__new__(LinearTracker)
        node = {
            "id": "id-1",
            "identifier": "HOM-1",
            "title": "Title",
            "description": "Body",
            "priority": 2,
            "url": "https://linear/issue/HOM-1",
            "branchName": "codex/HOM-1-title",
            "createdAt": "2026-04-28T00:00:00Z",
            "updatedAt": "2026-04-28T01:00:00Z",
            "state": {"name": "Todo"},
            "labels": {"nodes": [{"name": "Symphony"}]},
            "relations": {
                "nodes": [
                    {
                        "type": "blocks",
                        "relatedIssue": {
                            "id": "id-0",
                            "identifier": "HOM-0",
                            "state": {"name": "In Progress"},
                        },
                    }
                ]
            },
        }

        issue = tracker._normalize_issue(node)  # type: ignore[attr-defined]

        self.assertEqual(issue.identifier, "HOM-1")
        self.assertEqual(issue.labels, ["symphony"])
        self.assertEqual(issue.blocked_by[0].identifier, "HOM-0")


class FakeTracker:
    def __init__(self, issues: list[IssueRecord]) -> None:
        self.issues = issues
        self.state_updates: list[tuple[str, str]] = []

    def fetch_candidate_issues(self) -> list[IssueRecord]:
        return self.issues

    def fetch_issues_by_states(self, state_names: list[str]) -> list[IssueRecord]:
        return [issue for issue in self.issues if issue.state in state_names]

    def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> dict[str, str]:
        return {issue.id: issue.state for issue in self.issues if issue.id in issue_ids}

    def update_issue_state(self, issue_id: str, state_name: str) -> None:
        self.state_updates.append((issue_id, state_name))


class CommittingRunner:
    def run_issue(self, issue: IssueRecord, workspace, *, prompt: str, attempt: int | None) -> AgentResult:
        self.last_prompt = prompt
        (workspace.path / "agent.txt").write_text("done\n", encoding="utf-8")
        _run_git(workspace.path, ["add", "."])
        _run_git(
            workspace.path,
            [
                "commit",
                "-m",
                f"feat: complete {issue.identifier}\n\nEpisode-ID: fake-{issue.identifier}\nTask-ID: {issue.identifier}",
            ],
        )
        sha = _run_git(workspace.path, ["rev-parse", "HEAD"])
        return AgentResult(status="succeeded", message="ok", episode_id=f"fake-{issue.identifier}", commit_sha=sha)


class FailingRunner:
    def run_issue(self, issue: IssueRecord, workspace, *, prompt: str, attempt: int | None) -> AgentResult:
        return AgentResult(status="failed", message="boom")


if __name__ == "__main__":
    unittest.main()
