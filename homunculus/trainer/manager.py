from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

logger = logging.getLogger(__name__)

from ..config import HomunculusConfig
from ..dataset_builder.builder import DatasetBuilder
from ..models import AdapterManifest, EvaluationMetrics, utc_now
from ..storage import ArtifactStore

if TYPE_CHECKING:
    from ..evolution.merge import MergeManager, MergeResult
    from ..evolution.lineage import LineageTracker
    from ..evolution.validation import MergeValidator


class TrainingManager:
    def __init__(self, config: HomunculusConfig, store: ArtifactStore, builder: DatasetBuilder) -> None:
        self.config = config
        self.store = store
        self.builder = builder
        # Lazy-loaded evolution components
        self._merge_manager: "MergeManager | None" = None
        self._lineage_tracker: "LineageTracker | None" = None
        self._merge_validator: "MergeValidator | None" = None

    @property
    def merge_manager(self) -> "MergeManager":
        if self._merge_manager is None:
            from ..evolution.merge import MergeManager
            self._merge_manager = MergeManager(self.config, self.store)
        return self._merge_manager

    @property
    def lineage_tracker(self) -> "LineageTracker":
        if self._lineage_tracker is None:
            from ..evolution.lineage import LineageTracker
            self._lineage_tracker = LineageTracker(self.config, self.store)
        return self._lineage_tracker

    @property
    def merge_validator(self) -> "MergeValidator":
        if self._merge_validator is None:
            from ..evolution.validation import MergeValidator
            self._merge_validator = MergeValidator(self.config)
        return self._merge_validator

    def should_train_sft(self, new_verified_samples: int, last_successful_train_at: str | None, now: datetime | None = None) -> bool:
        if new_verified_samples >= self.config.thresholds.train_after_samples:
            return True
        if not last_successful_train_at:
            return new_verified_samples > 0
        now = now or datetime.now(timezone.utc)
        last = datetime.fromisoformat(last_successful_train_at.replace("Z", "+00:00"))
        return now - last >= timedelta(days=self.config.thresholds.train_after_days)

    def run_sft(self, simulate: bool = False) -> AdapterManifest:
        self.store.ensure_layout()
        snapshot = self.builder.materialize_sft_snapshot()
        candidate_id = uuid.uuid4().hex[:12]
        adapter_root = Path(self.config.student.adapter_root)
        if not adapter_root.is_absolute():
            adapter_root = (self.config.paths.root / adapter_root).resolve()
        adapter_path = adapter_root / candidate_id
        adapter_path.mkdir(parents=True, exist_ok=True)
        command = [
            *self.config.student.train_command,
            "--model",
            self.config.student.model_id,
            "--train",
            "--data",
            snapshot.snapshot_path,
            "--adapter-path",
            str(adapter_path),
            "--batch-size",
            str(self.config.student.batch_size),
            "--grad-accumulation-steps",
            str(self.config.student.grad_accumulation_steps),
        ]
        if self.config.student.prompt_masking:
            command.append("--mask-prompt")
        # Aggregate contributing episode ids from the snapshot's split-keyed map.
        # Used downstream by lineage tracking (register_lora) so future merges can
        # trace which episodes contributed to a candidate.
        contributing_episodes: list[str] = []
        seen: set[str] = set()
        for split_episodes in (snapshot.selected_episode_ids or {}).values():
            for ep_id in split_episodes:
                if ep_id and ep_id not in seen:
                    seen.add(ep_id)
                    contributing_episodes.append(ep_id)
        manifest = AdapterManifest(
            model_id=self.config.student.model_id,
            base_model=self.config.student.model_id,
            adapter_path=str(adapter_path),
            dataset_snapshot=snapshot.snapshot_id,
            snapshot_path=snapshot.snapshot_path,
            trainer="mlx-lm",
            metrics={},
            status="training",
            created_at=utc_now(),
            candidate_id=candidate_id,
            lineage=[snapshot.snapshot_id],
            training_command=command,
            sample_counts=snapshot.sample_counts,
            self_generated_ratio=snapshot.self_generated_ratio,
            evaluation_status="pending",
            contributing_episode_ids=contributing_episodes,
        )
        self.store.register_candidate(manifest)
        if simulate:
            (adapter_path / "adapter.safetensors").write_text("simulated-adapter", encoding="utf-8")
            manifest.status = "trained"
            manifest.training_output = {"mode": "simulated"}
            self.store.update_candidate(manifest)
            return manifest
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=self.config.student.train_timeout_seconds,
        )
        if completed.returncode != 0:
            manifest.status = "failed"
            manifest.training_output = {"stderr": completed.stderr, "stdout": completed.stdout, "returncode": completed.returncode}
            self.store.update_candidate(manifest)
            raise RuntimeError(f"SFT training failed: {completed.stderr}")
        manifest.status = "trained"
        manifest.training_output = {"stdout": completed.stdout, "returncode": completed.returncode}
        self.store.update_candidate(manifest)
        return manifest

    def read_sft_promotion_count(self) -> int:
        registry = self.store.load_registry()
        return sum(1 for item in registry.get("candidates", []) if item.get("status") == "promoted" and item.get("trainer") == "mlx-lm")

    def evaluate_candidate(self, candidate: AdapterManifest, metrics: EvaluationMetrics) -> AdapterManifest:
        allowed, reasons = self._promotion_gates(candidate, metrics)
        candidate.metrics = metrics.to_dict()
        candidate.status = "evaluated"
        candidate.evaluation_status = "eligible" if allowed else "ineligible"
        candidate.promotion_reason = "" if allowed else "; ".join(reasons)
        self.store.update_candidate(candidate)
        return candidate

    def promote_candidate(self, candidate: AdapterManifest) -> AdapterManifest:
        if not candidate.metrics:
            raise RuntimeError("Candidate must be evaluated before promotion.")
        metrics = EvaluationMetrics.from_dict(candidate.metrics)
        allowed, reasons = self._promotion_gates(candidate, metrics)
        if allowed:
            candidate.status = "promoted"
            candidate.evaluation_status = "eligible"
            candidate.promotion_reason = "passed promotion gates"
            self.store.update_candidate(candidate)
            self.store.set_active_candidate(candidate)
            # Register in lineage so subsequent merges can trace ancestry.
            # Lineage is observability — failures must not crash promotion.
            try:
                self.lineage_tracker.register_lora(
                    candidate,
                    episode_ids=list(candidate.contributing_episode_ids or []),
                )
            except Exception as exc:  # noqa: BLE001 — observability path
                logger.warning(
                    "Failed to register candidate %s in lineage: %s",
                    candidate.candidate_id,
                    exc,
                )
            return candidate
        candidate.status = "rejected"
        candidate.evaluation_status = "ineligible"
        candidate.promotion_reason = "; ".join(reasons)
        self.store.update_candidate(candidate)
        raise RuntimeError(candidate.promotion_reason)

    def rollback_active_model(self, reason: str) -> AdapterManifest | None:
        registry = self.store.load_registry()
        history = registry.get("history", [])
        if not history:
            return None
        candidate = self.store.get_candidate(history[-1])
        if not candidate:
            return None
        candidate.status = "promoted"
        candidate.evaluation_status = "eligible"
        candidate.promotion_reason = f"rollback target: {reason}"
        self.store.update_candidate(candidate)
        self.store.set_active_candidate(candidate)
        return candidate

    def _promotion_gates(self, candidate: AdapterManifest, metrics: EvaluationMetrics) -> tuple[bool, list[str]]:
        current = self.store.active_candidate()
        current_metrics = EvaluationMetrics.from_dict(current.metrics) if current and current.metrics else EvaluationMetrics(
            compile_pass_rate=0.0,
            task_success_rate=0.0,
            average_retries_to_success=999.0,
            regression_count=0,
            memory_usefulness_score=0.0,
            tool_misuse_rate=1.0,
        )
        allowed = True
        reasons: list[str] = []
        if self.config.promotion.allow_zero_canary_regressions and metrics.regression_count != 0:
            allowed = False
            reasons.append("candidate has canary regressions")
        if metrics.task_success_rate < current_metrics.task_success_rate + self.config.promotion.min_task_success_delta:
            allowed = False
            reasons.append("task success delta too small")
        if metrics.tool_misuse_rate > current_metrics.tool_misuse_rate + self.config.promotion.max_tool_misuse_increase:
            allowed = False
            reasons.append("tool misuse increased")
        if metrics.average_retries_to_success > current_metrics.average_retries_to_success + self.config.promotion.max_retry_increase:
            allowed = False
            reasons.append("retry count increased")
        if metrics.compile_pass_rate < current_metrics.compile_pass_rate:
            allowed = False
            reasons.append("compile pass rate regressed")
        if not candidate.snapshot_path or not Path(candidate.snapshot_path).exists():
            allowed = False
            reasons.append("training snapshot is missing")
        return allowed, reasons

    # ─────────────────────────────────────────────────────────────────────────
    # Evolution / Merge Integration
    # ─────────────────────────────────────────────────────────────────────────

    def _get_consecutive_merge_failures(self) -> int:
        """Get consecutive merge failures from persistent state.

        Defaults to 0 on any error: missing file, corrupt JSON, missing key,
        non-int value, negative value. This prevents an interrupted/corrupt
        state file from crashing the daemon's _check_evolution cycle.
        """
        state_file = self.config.paths.runtime_dir / "evolution_state.json"
        if not state_file.exists():
            return 0
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        if not isinstance(data, dict):
            return 0
        value = data.get("consecutive_merge_failures", 0)
        # bool is a subclass of int — exclude it explicitly.
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return 0
        return value

    def _set_consecutive_merge_failures(self, count: int) -> None:
        """Persist consecutive merge failure count atomically.

        Writes to a temp file then os.replace's into place so an
        interrupted write never produces a partial JSON.
        """
        state_file = self.config.paths.runtime_dir / "evolution_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = state_file.with_suffix(state_file.suffix + ".tmp")
        tmp_file.write_text(
            json.dumps({"consecutive_merge_failures": int(max(0, count))}),
            encoding="utf-8",
        )
        os.replace(tmp_file, state_file)

    def should_merge(self) -> bool:
        """Check if we should trigger a merge operation."""
        if not self.config.evolution.enabled:
            return False
        return self.merge_manager.should_merge()

    def run_merge(self) -> "MergeResult":
        """Execute a merge operation with validation.

        Returns:
            MergeResult with success status and details
        """
        from ..evolution.merge import MergeResult

        candidates = self.merge_manager.get_merge_candidates()
        if not candidates:
            return MergeResult(success=False, error_message="No candidates to merge")

        # Execute merge
        result = self.merge_manager.merge(candidates)

        if not result.success:
            self._set_consecutive_merge_failures(self._get_consecutive_merge_failures() + 1)
            return result

        # Validate the merge
        manifest = result.merge_manifest
        if manifest is None:
            self._set_consecutive_merge_failures(self._get_consecutive_merge_failures() + 1)
            return MergeResult(success=False, error_message="Merge succeeded but no manifest returned")

        validation = self.merge_validator.validate(manifest)

        if not validation.passed:
            self._set_consecutive_merge_failures(self._get_consecutive_merge_failures() + 1)
            manifest.status = "failed"
            manifest.validation_results = validation.to_dict()
            manifest.error_message = f"Validation failed: {validation.stages[-1].message}"
            self.store.update_merge(manifest)
            return MergeResult(
                success=False,
                error_message=manifest.error_message,
                merge_manifest=manifest,
            )

        # Success! Record in lineage
        manifest.status = "validated"
        manifest.validation_results = validation.to_dict()
        self.store.update_merge(manifest)

        # Register in lineage
        output_model_id = f"{manifest.target_base}-gen{self.lineage_tracker.get_current_generation() + 1}"
        self.lineage_tracker.register_merge(manifest, output_model_id)

        self._set_consecutive_merge_failures(0)  # Reset on success
        return result

    def should_generate_merge_failure_task(self) -> bool:
        """Check if we should generate an introspection task for merge failures."""
        return self._get_consecutive_merge_failures() >= self.config.evolution.max_merge_attempts

    def reset_merge_failure_count(self) -> None:
        """Reset the consecutive merge failure counter."""
        self._set_consecutive_merge_failures(0)
