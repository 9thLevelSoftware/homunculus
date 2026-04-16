from __future__ import annotations

from datetime import datetime, timedelta, timezone
import subprocess
from pathlib import Path
import uuid

from ..config import HomunculusConfig
from ..dataset_builder.builder import DatasetBuilder
from ..models import AdapterManifest, EvaluationMetrics, utc_now
from ..storage import ArtifactStore


class TrainingManager:
    def __init__(self, config: HomunculusConfig, store: ArtifactStore, builder: DatasetBuilder) -> None:
        self.config = config
        self.store = store
        self.builder = builder

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

    def promote_candidate(self, candidate: AdapterManifest, human_approved: bool = False) -> AdapterManifest:
        if self.config.promotion.require_human_approval and not human_approved:
            raise RuntimeError("Human approval is required before promotion.")
        if not candidate.metrics:
            raise RuntimeError("Candidate must be evaluated before promotion.")
        metrics = EvaluationMetrics.from_dict(candidate.metrics)
        allowed, reasons = self._promotion_gates(candidate, metrics)
        candidate.human_approved = human_approved
        if allowed:
            candidate.status = "promoted"
            candidate.evaluation_status = "eligible"
            candidate.promotion_reason = "passed promotion gates"
            self.store.update_candidate(candidate)
            self.store.set_active_candidate(candidate)
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
