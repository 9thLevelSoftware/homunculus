from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from ..models import utc_now
from .merge_gate import MergeGate
from .models import IssueRecord, RetryEntry, RunAttempt, SymphonyConfig, WorkspaceRecord
from .runner import AgentRunner, build_agent_runner, render_issue_prompt
from .tracker import IssueTracker
from .workspace import WorkspaceManager


class SymphonyOrchestratorError(RuntimeError):
    pass


class SymphonyOrchestrator:
    def __init__(
        self,
        config: SymphonyConfig,
        tracker: IssueTracker,
        *,
        runner: AgentRunner | None = None,
        workspace_manager: WorkspaceManager | None = None,
        merge_gate: MergeGate | None = None,
    ) -> None:
        self.config = config
        self.tracker = tracker
        self.runner = runner or build_agent_runner(config)
        self.workspace_manager = workspace_manager or WorkspaceManager(config)
        self.merge_gate = merge_gate or MergeGate(config)
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.config.logs_dir.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> dict[str, Any]:
        state = self.load_state()
        self._reconcile_terminal_work(state)
        candidates = self.tracker.fetch_candidate_issues()
        dispatchable = self._eligible_candidates(candidates, state)
        executed = 0
        succeeded = 0
        failed = 0

        for issue in dispatchable[: self._available_slots(state)]:
            state.setdefault("claimed", {})[issue.id] = issue.identifier
            self.save_state(state)
            run = self._execute_issue(issue, state)
            executed += 1
            if run.status == "succeeded":
                succeeded += 1
                state.setdefault("completed", {})[issue.id] = issue.identifier
                state.get("retry_attempts", {}).pop(issue.id, None)
                state.get("claimed", {}).pop(issue.id, None)
            else:
                failed += 1
                self._schedule_retry(issue, state, run.error or run.status)
            self.save_state(state)

        state["updated_at"] = utc_now()
        self.save_state(state)
        return {
            "status": "executed" if executed else "idle",
            "executed": executed,
            "succeeded": succeeded,
            "failed": failed,
            "claimed": len(state.get("claimed", {})),
            "retrying": len(state.get("retry_attempts", {})),
        }

    def run_forever(self) -> None:
        while True:
            result = self.run_once()
            print(
                "Symphony cycle: "
                f"{result['status']}, {result['executed']} executed, "
                f"{result['succeeded']} succeeded, {result['failed']} failed"
            )
            time.sleep(max(self.config.polling.interval_ms, 1000) / 1000)

    def load_state(self) -> dict[str, Any]:
        path = self.config.state_path
        if not path.exists():
            return self._fresh_state()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._fresh_state()
        if not isinstance(payload, dict):
            return self._fresh_state()
        payload.setdefault("claimed", {})
        payload.setdefault("running", {})
        payload.setdefault("completed", {})
        payload.setdefault("retry_attempts", {})
        return payload

    def save_state(self, state: dict[str, Any]) -> None:
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = utc_now()
        tmp_path = self.config.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
        os.replace(tmp_path, self.config.state_path)

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = _load_jsonl(self.config.runs_path)
        return rows[-limit:]

    def _execute_issue(self, issue: IssueRecord, state: dict[str, Any]) -> RunAttempt:
        retry = state.get("retry_attempts", {}).get(issue.id)
        attempt = int(retry.get("attempt", 1)) if isinstance(retry, dict) else None
        workspace = self.workspace_manager.ensure_workspace(issue)
        run = RunAttempt(
            issue_id=issue.id,
            issue_identifier=issue.identifier,
            attempt=attempt,
            workspace_path=str(workspace.path),
            branch_name=workspace.branch_name,
        )
        self._append_log(issue, "workspace_prepared", workspace.to_dict())

        try:
            if issue.state.lower() == "todo":
                self._safe_state_update(issue.id, self.config.homunculus.in_progress_state)
            self._run_hook("after_create", self.config.hooks.after_create, workspace, only_if=workspace.created_now)
            self._run_hook("before_run", self.config.hooks.before_run, workspace)
            prompt = render_issue_prompt(self.config, issue, attempt)
            agent_result = self.runner.run_issue(issue, workspace, prompt=prompt, attempt=attempt)
            self._append_log(issue, "agent_result", agent_result.to_dict())
            if not agent_result.succeeded:
                run.complete("failed", error=agent_result.message, agent_result=agent_result)
                return self._persist_run(run)

            gates = self.merge_gate.run_gates(workspace)
            self._append_log(issue, "merge_gates", {"results": [item.to_dict() for item in gates]})
            if not all(item.passed for item in gates):
                run.complete(
                    "failed",
                    error="merge gate failed",
                    agent_result=agent_result,
                    merge_gates=gates,
                )
                return self._persist_run(run)

            merge_commit = None
            merged = False
            if self.config.homunculus.auto_merge:
                merge_commit = self.merge_gate.merge_branch(workspace)
                merged = True
            self._safe_state_update(issue.id, self.config.homunculus.done_state)
            run.complete(
                "succeeded",
                agent_result=agent_result,
                merge_gates=gates,
                merged=merged,
                merge_commit=merge_commit,
            )
            return self._persist_run(run)
        except Exception as exc:
            run.complete("failed", error=f"{type(exc).__name__}: {exc}")
            self._append_log(issue, "run_error", {"error": run.error})
            if self.config.homunculus.failed_state:
                self._safe_state_update(issue.id, self.config.homunculus.failed_state)
            return self._persist_run(run)
        finally:
            try:
                self._run_hook("after_run", self.config.hooks.after_run, workspace)
            except Exception as exc:
                self._append_log(issue, "after_run_hook_error", {"error": str(exc)})

    def _persist_run(self, run: RunAttempt) -> RunAttempt:
        self.config.runs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run.to_dict(), ensure_ascii=True) + "\n")
        return run

    def _eligible_candidates(
        self, candidates: list[IssueRecord], state: dict[str, Any]
    ) -> list[IssueRecord]:
        active = {item.lower() for item in self.config.tracker.active_states}
        terminal = {item.lower() for item in self.config.tracker.terminal_states}
        now_ms = _monotonic_ms()
        retry_attempts = state.setdefault("retry_attempts", {})
        claimed = state.setdefault("claimed", {})
        eligible: list[IssueRecord] = []
        for issue in candidates:
            if not issue.id or not issue.identifier or not issue.title or not issue.state:
                continue
            if issue.state.lower() not in active or issue.state.lower() in terminal:
                continue
            retry = retry_attempts.get(issue.id)
            if isinstance(retry, dict) and int(retry.get("due_at_ms", 0)) > now_ms:
                continue
            if issue.id in claimed and issue.id not in retry_attempts:
                continue
            if issue.state.lower() == "todo" and self._has_active_blocker(issue, terminal):
                continue
            eligible.append(issue)
        eligible.sort(key=self._sort_key)
        return eligible

    def _available_slots(self, state: dict[str, Any]) -> int:
        running_count = len(state.get("running", {}))
        return max(self.config.agent.max_concurrent_agents - running_count, 0)

    def _has_active_blocker(self, issue: IssueRecord, terminal: set[str]) -> bool:
        for blocker in issue.blocked_by:
            if blocker.state and blocker.state.lower() not in terminal:
                return True
        return False

    def _sort_key(self, issue: IssueRecord) -> tuple[int, str, str]:
        priority = issue.priority if issue.priority is not None else 999
        return (priority, issue.created_at or "", issue.identifier)

    def _schedule_retry(self, issue: IssueRecord, state: dict[str, Any], error: str) -> None:
        retries = state.setdefault("retry_attempts", {})
        previous = retries.get(issue.id, {})
        previous_attempt = int(previous.get("attempt", 0)) if isinstance(previous, dict) else 0
        attempt = previous_attempt + 1
        delay_ms = min(10000 * (2 ** max(attempt - 1, 0)), self.config.agent.max_retry_backoff_ms)
        retries[issue.id] = RetryEntry(
            issue_id=issue.id,
            identifier=issue.identifier,
            attempt=attempt,
            due_at_ms=_monotonic_ms() + delay_ms,
            error=error,
        ).to_dict()

    def _reconcile_terminal_work(self, state: dict[str, Any]) -> None:
        claimed = state.setdefault("claimed", {})
        if not claimed:
            return
        terminal = {item.lower() for item in self.config.tracker.terminal_states}
        try:
            states = self.tracker.fetch_issue_states_by_ids(list(claimed.keys()))
        except Exception:
            return
        for issue_id, state_name in states.items():
            if state_name.lower() in terminal:
                claimed.pop(issue_id, None)
                state.get("retry_attempts", {}).pop(issue_id, None)

    def _run_hook(
        self,
        name: str,
        script: str | None,
        workspace: WorkspaceRecord,
        *,
        only_if: bool = True,
    ) -> None:
        if not script or not only_if:
            return
        completed = subprocess.run(
            script,
            cwd=workspace.path,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(self.config.hooks.timeout_ms / 1000, 1),
        )
        self._append_log(
            IssueRecord(id="-", identifier=workspace.workspace_key, title="-"),
            f"hook_{name}",
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
        if completed.returncode != 0 and name in {"after_create", "before_run"}:
            raise SymphonyOrchestratorError(f"{name} hook failed: {completed.stderr}")

    def _safe_state_update(self, issue_id: str, state_name: str) -> None:
        try:
            self.tracker.update_issue_state(issue_id, state_name)
        except Exception as exc:
            self._append_global_log("tracker_state_update_failed", {"issue_id": issue_id, "state": state_name, "error": str(exc)})

    def _append_log(self, issue: IssueRecord, event: str, payload: dict[str, Any]) -> None:
        self._append_global_log(
            event,
            {"issue_id": issue.id, "identifier": issue.identifier, **payload},
            path=self.config.logs_dir / f"{issue.identifier}.jsonl",
        )

    def _append_global_log(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        path: Path | None = None,
    ) -> None:
        target = path or (self.config.logs_dir / "symphony.jsonl")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": event, "timestamp": utc_now(), **payload}, ensure_ascii=True) + "\n")

    def _fresh_state(self) -> dict[str, Any]:
        return {
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "claimed": {},
            "running": {},
            "completed": {},
            "retry_attempts": {},
            "codex_totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows
