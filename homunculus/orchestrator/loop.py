from __future__ import annotations

from uuid import uuid4

from ..config import HomunculusConfig
from ..dataset_builder.builder import DatasetBuilder
from ..memory_client.base import MemoryContract
from ..models import EpisodeRecord, StudentResponse, TaskRequest, TeacherResponse, utc_now
from ..policy import GuardrailEngine
from ..storage import ArtifactStore
from ..task_runner.runner import TaskRunner, WorkspacePreflightError


class EpisodeOrchestrator:
    def __init__(
        self,
        config: HomunculusConfig,
        store: ArtifactStore,
        memory_client: MemoryContract,
        teacher,
        student,
        task_runner: TaskRunner,
        dataset_builder: DatasetBuilder,
        guardrails: GuardrailEngine,
    ) -> None:
        self.config = config
        self.store = store
        self.memory_client = memory_client
        self.teacher = teacher
        self.student = student
        self.task_runner = task_runner
        self.dataset_builder = dataset_builder
        self.guardrails = guardrails

    def run_episode(self, task: TaskRequest) -> EpisodeRecord:
        episode_id = uuid4().hex
        attempt_index = self._attempt_index(task.task_id) + 1
        patch_path = str(self.store.write_patch_artifact(episode_id, ""))
        stage = "assess"
        workspace = None
        memories = []
        teacher_response = TeacherResponse(plan=[], candidate_patch=None, rationale=None, raw={})
        student_response = StudentResponse(text=None, raw={})
        diff_hash = "unavailable"
        outcome = "error"
        review_status = "needs_review"
        verification_passed = False
        memory_refs: list[str] = []
        test_results = []
        lint_results = []
        failure_stage: str | None = None
        error_type: str | None = None
        error_message: str | None = None
        curation = {"sft_added": 0, "dpo_added": 0}

        self.store.ensure_layout()
        try:
            workspace = self.config.workspaces[task.workspace]
            self.store.append_event("assess", {"episode_id": episode_id, "task_id": task.task_id, "workspace": task.workspace, "timestamp": utc_now()})

            stage = "preflight"
            snapshot = self.task_runner.require_clean_workspace(workspace)
            self.store.append_event("preflight", {"episode_id": episode_id, "task_id": task.task_id, "head": snapshot.head, "timestamp": utc_now()})

            stage = "recall"
            memories = self.memory_client.get_active_context(task.prompt)
            memory_refs = [item.id for item in memories]
            self.store.append_event("recall", {"episode_id": episode_id, "task_id": task.task_id, "memory_ids": memory_refs, "timestamp": utc_now()})

            stage = "plan"
            student_response = self.student.suggest(task.prompt)
            teacher_response = self.teacher.generate(task, memories, student_response.text)
            if teacher_response.candidate_patch is not None:
                patch_path = str(self.store.write_patch_artifact(episode_id, teacher_response.candidate_patch))
            self.store.append_event(
                "plan",
                {
                    "episode_id": episode_id,
                    "task_id": task.task_id,
                    "plan": teacher_response.plan,
                    "has_patch": bool(teacher_response.candidate_patch),
                    "timestamp": utc_now(),
                },
            )

            stage = "preflight"
            decision = self.guardrails.evaluate(task.prompt, teacher_response.candidate_patch, memories)
            if not decision.allowed:
                outcome = "blocked"
                review_status = "rejected"
                verification_passed = False
                diff_hash = "blocked"
                failure_stage = "preflight"
                memory_refs = [*decision.memory_refs, *memory_refs]
                error_type = "GuardrailBlocked"
                error_message = "; ".join(decision.blocked_reasons)
                self.store.append_event("preflight_blocked", {"episode_id": episode_id, "task_id": task.task_id, "reasons": decision.blocked_reasons, "timestamp": utc_now()})
                self.memory_client.store_memory("warning", error_message, {"episode_id": episode_id, "task_id": task.task_id, "workspace": task.workspace})
                stage = None
            else:
                stage = "execute"
                execution = self.task_runner.execute_patch(workspace, episode_id, teacher_response.candidate_patch)
                test_results = [item for item in execution.verification_results if item.kind == "test"]
                lint_results = [item for item in execution.verification_results if item.kind != "test"]
                verification_passed = all(item.passed for item in execution.verification_results)
                outcome = "accepted" if verification_passed else "reverted"
                review_status = "approved" if verification_passed else "needs_review"
                diff_hash = execution.diff_hash
                if execution.canonical_patch is not None:
                    patch_path = str(self.store.write_patch_artifact(episode_id, execution.canonical_patch))
                self.store.append_event(
                    "execute",
                    {
                        "episode_id": episode_id,
                        "task_id": task.task_id,
                        "diff_hash": execution.diff_hash,
                        "reverted": execution.reverted,
                        "timestamp": utc_now(),
                    },
                )

                stage = "reflect"
                self.memory_client.record_outcome(
                    episode_id,
                    verification_passed,
                    {"workspace": task.workspace, "diff_hash": diff_hash, "outcome": outcome},
                )
                self.store.append_event("reflect", {"episode_id": episode_id, "task_id": task.task_id, "worked": verification_passed, "timestamp": utc_now()})
                if not verification_passed:
                    self.memory_client.store_memory(
                        "failure",
                        f"Task {task.task_id} failed verification",
                        {"episode_id": episode_id, "task_id": task.task_id, "workspace": task.workspace, "diff_hash": diff_hash},
                    )
                    if self._failure_count(task.task_id) + 1 >= self.config.thresholds.failure_growth_threshold:
                        self.memory_client.store_memory(
                            "growth",
                            f"Task {task.task_id} has repeated failures and needs decomposition.",
                            {"episode_id": episode_id, "task_id": task.task_id, "failure_count": self._failure_count(task.task_id) + 1},
                        )

                stage = "curate"
                episode_for_curation = self._build_episode(
                    episode_id=episode_id,
                    task=task,
                    attempt_index=attempt_index,
                    teacher_response=teacher_response,
                    student_response=student_response,
                    diff_hash=diff_hash,
                    test_results=test_results,
                    lint_results=lint_results,
                    outcome=outcome,
                    memory_refs=memory_refs,
                    patch_path=patch_path,
                    review_status=review_status,
                    verification_passed=verification_passed,
                    failure_stage=None,
                    error_type=None,
                    error_message=None,
                )
                curation = self.dataset_builder.ingest_episode(episode_for_curation)
                self.store.append_event(
                    "curate",
                    {
                        "episode_id": episode_id,
                        "task_id": task.task_id,
                        "sft_added": curation["sft_added"],
                        "dpo_added": curation["dpo_added"],
                        "timestamp": utc_now(),
                    },
                )
                stage = None
        except WorkspacePreflightError as exc:
            outcome = "blocked"
            review_status = "rejected"
            diff_hash = "blocked"
            verification_passed = False
            failure_stage = "preflight"
            error_type = type(exc).__name__
            error_message = str(exc)
            self.store.append_event("preflight_blocked", {"episode_id": episode_id, "task_id": task.task_id, "reasons": [str(exc)], "timestamp": utc_now()})
            try:
                self.memory_client.store_memory("warning", str(exc), {"episode_id": episode_id, "task_id": task.task_id, "workspace": task.workspace})
            except Exception:
                pass
        except Exception as exc:
            failure_stage = stage or "unknown"
            error_type = type(exc).__name__
            error_message = str(exc)
            outcome = "error"
            verification_passed = False
            review_status = "needs_review"
            try:
                self.memory_client.store_memory(
                    "failure",
                    f"Episode {episode_id} failed during {failure_stage}: {error_message}",
                    {"episode_id": episode_id, "task_id": task.task_id, "failure_stage": failure_stage, "error_type": error_type},
                )
            except Exception:
                pass
            self.store.append_event(
                "episode_failed",
                {
                    "episode_id": episode_id,
                    "task_id": task.task_id,
                    "failure_stage": failure_stage,
                    "error_type": error_type,
                    "error_message": error_message,
                    "timestamp": utc_now(),
                },
            )

        episode = self._build_episode(
            episode_id=episode_id,
            task=task,
            attempt_index=attempt_index,
            teacher_response=teacher_response,
            student_response=student_response,
            diff_hash=diff_hash,
            test_results=test_results,
            lint_results=lint_results,
            outcome=outcome,
            memory_refs=memory_refs,
            patch_path=patch_path,
            review_status=review_status,
            verification_passed=verification_passed,
            failure_stage=failure_stage,
            error_type=error_type,
            error_message=error_message,
        )
        self.store.append_episode(episode)
        if outcome != "error":
            self.store.append_event(
                "episode_completed",
                {
                    "episode_id": episode_id,
                    "task_id": task.task_id,
                    "outcome": outcome,
                    "verification_passed": verification_passed,
                    "timestamp": utc_now(),
                },
            )
        return episode

    def _build_episode(
        self,
        episode_id: str,
        task: TaskRequest,
        attempt_index: int,
        teacher_response: TeacherResponse,
        student_response: StudentResponse,
        diff_hash: str,
        test_results,
        lint_results,
        outcome: str,
        memory_refs: list[str],
        patch_path: str,
        review_status: str,
        verification_passed: bool,
        failure_stage: str | None,
        error_type: str | None,
        error_message: str | None,
    ) -> EpisodeRecord:
        return EpisodeRecord(
            episode_id=episode_id,
            task_id=task.task_id,
            workspace=task.workspace,
            prompt=task.prompt,
            plan=teacher_response.plan,
            teacher_output={"rationale": teacher_response.rationale, "raw": teacher_response.raw},
            student_output=student_response.raw | {"text": student_response.text},
            diff_hash=diff_hash,
            test_results=test_results,
            lint_results=lint_results,
            outcome=outcome,
            timestamp=utc_now(),
            attempt_index=attempt_index,
            memory_refs=memory_refs,
            patch=self.store.read_patch_artifact(episode_id),
            patch_path=patch_path,
            review_status=review_status,
            comparison_group=task.comparison_group,
            failure_count=self._failure_count(task.task_id) + (0 if outcome == "accepted" else 1),
            verification_passed=verification_passed,
            failure_stage=failure_stage,
            error_type=error_type,
            error_message=error_message,
        )

    def _attempt_index(self, task_id: str) -> int:
        episodes = self.store.load_episodes()
        return sum(1 for item in episodes if item.task_id == task_id)

    def _failure_count(self, task_id: str) -> int:
        episodes = self.store.load_episodes()
        return sum(1 for item in episodes if item.task_id == task_id and item.outcome != "accepted")
