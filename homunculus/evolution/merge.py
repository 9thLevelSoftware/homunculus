"""LoRA merge pipeline for weight evolution."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..config import HomunculusConfig
from ..models import AdapterManifest, MergeManifest, utc_now
from ..storage import ArtifactStore


def detect_backend() -> Literal["mergekit", "mlx"]:
    """Detect available merge backend based on hardware.

    Returns:
        "mlx" on Apple Silicon Macs, "mergekit" otherwise (Windows/Linux with CUDA or CPU fallback)
    """
    system = platform.system()

    if system == "Darwin":
        # macOS - check for Apple Silicon
        machine = platform.machine()
        if machine == "arm64":
            return "mlx"

    # Windows/Linux - check for CUDA
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "mergekit"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to mergekit (can run on CPU, just slower)
    return "mergekit"


@dataclass
class MergeResult:
    """Result of a merge operation."""

    success: bool
    output_path: str | None = None
    error_message: str | None = None
    merge_manifest: MergeManifest | None = None


class MergeManager:
    """Orchestrates LoRA merge operations."""

    def __init__(self, config: HomunculusConfig, store: ArtifactStore) -> None:
        self.config = config
        self.store = store
        self.backend = self._select_backend()

    def _select_backend(self) -> Literal["mergekit", "mlx"]:
        """Select merge backend based on config or auto-detection."""
        if self.config.evolution.merge_backend == "auto":
            return detect_backend()
        return self.config.evolution.merge_backend  # type: ignore[return-value]

    def should_merge(self) -> bool:
        """Check if we have enough promoted LoRAs to trigger a merge."""
        registry = self.store.load_registry()
        promoted = [
            c for c in registry.get("candidates", [])
            if c.get("status") == "promoted"
        ]
        # Count LoRAs since last merge
        merges = self.store.load_merges()
        last_merge_at = max((m.created_at for m in merges), default=None)

        if last_merge_at:
            # Only count LoRAs promoted after last merge
            promoted_since = [
                c for c in promoted
                if c.get("created_at", "") > last_merge_at
            ]
        else:
            promoted_since = promoted

        return len(promoted_since) >= self.config.evolution.auto_merge_after_loras

    def get_merge_candidates(self) -> list[AdapterManifest]:
        """Get the LoRAs that should be merged."""
        registry = self.store.load_registry()
        promoted = [
            AdapterManifest.from_dict(c)
            for c in registry.get("candidates", [])
            if c.get("status") == "promoted"
        ]

        merges = self.store.load_merges()
        last_merge_at = max((m.created_at for m in merges), default=None)

        if last_merge_at:
            return [c for c in promoted if c.created_at > last_merge_at]
        return promoted

    def merge(self, loras: list[AdapterManifest], method: str = "linear") -> MergeResult:
        """Execute a merge operation.

        Args:
            loras: List of LoRA adapters to merge
            method: Merge method ("linear", "ties", "dare_ties")

        Returns:
            MergeResult with success status and output path or error
        """
        import uuid

        if not loras:
            return MergeResult(
                success=False,
                error_message="No LoRAs provided for merging",
            )

        merge_id = f"merge-{uuid.uuid4().hex[:8]}"

        manifest = MergeManifest(
            merge_id=merge_id,
            source_loras=[lora.candidate_id for lora in loras if lora.candidate_id],
            target_base=loras[0].base_model,  # All should share same base
            merge_method=method,
            status="merging",
        )
        self.store.append_merge(manifest)

        try:
            if self.backend == "mergekit":
                result = self._merge_with_mergekit(manifest, loras)
            else:
                result = self._merge_with_mlx(manifest, loras)

            if result.success:
                manifest.status = "complete"
                manifest.completed_at = utc_now()
                manifest.output_path = result.output_path
            else:
                manifest.status = "failed"
                manifest.error_message = result.error_message

            self.store.update_merge(manifest)
            result.merge_manifest = manifest
            return result

        except Exception as e:
            manifest.status = "failed"
            manifest.error_message = str(e)
            self.store.update_merge(manifest)
            return MergeResult(success=False, error_message=str(e), merge_manifest=manifest)

    def _merge_with_mergekit(
        self,
        manifest: MergeManifest,
        loras: list[AdapterManifest],
    ) -> MergeResult:
        """Merge LoRAs using mergekit."""
        try:
            import yaml
        except ImportError:
            return MergeResult(
                success=False,
                error_message="pyyaml not installed. Run: pip install pyyaml",
            )

        import tempfile

        # Prepare output directory
        models_dir = self.config.paths.models_dir
        output_dir = models_dir / "merged" / manifest.merge_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate mergekit config YAML
        config = self._generate_mergekit_config(manifest, loras)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.safe_dump(config, f)
            config_path = f.name

        try:
            # Run mergekit-yaml
            cmd = [
                "mergekit-yaml",
                config_path,
                str(output_dir),
                "--copy-tokenizer",
            ]

            if manifest.merge_method == "ties":
                cmd.extend(["--allow-crimes"])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.evolution.validation_timeout_seconds,
            )

            if result.returncode != 0:
                return MergeResult(
                    success=False,
                    error_message=f"mergekit failed: {result.stderr}",
                )

            return MergeResult(
                success=True,
                output_path=str(output_dir),
            )

        except FileNotFoundError:
            return MergeResult(
                success=False,
                error_message="mergekit not found. Install with: pip install mergekit",
            )
        except subprocess.TimeoutExpired:
            return MergeResult(
                success=False,
                error_message=f"mergekit timed out after {self.config.evolution.validation_timeout_seconds}s",
            )
        finally:
            Path(config_path).unlink(missing_ok=True)

    def _generate_mergekit_config(
        self,
        manifest: MergeManifest,
        loras: list[AdapterManifest],
    ) -> dict[str, Any]:
        """Generate mergekit YAML config for the merge."""
        base_model = loras[0].base_model

        # Linear merge: weighted average of base + all LoRAs
        if manifest.merge_method == "linear":
            lora_weight = 1.0 / len(loras)

            models = [{"model": base_model, "parameters": {"weight": 0.5}}]
            for lora in loras:
                models.append({
                    "model": lora.adapter_path,
                    "parameters": {"weight": lora_weight * 0.5}
                })

            return {
                "merge_method": "linear",
                "models": models,
                "dtype": "bfloat16",
            }

        # TIES merge: resolve interference between LoRAs
        elif manifest.merge_method == "ties":
            return {
                "merge_method": "ties",
                "base_model": base_model,
                "models": [
                    {"model": lora.adapter_path, "parameters": {"density": 0.5, "weight": 1.0}}
                    for lora in loras
                ],
                "dtype": "bfloat16",
                "parameters": {"normalize": True},
            }

        # DARE: Drop And REscale
        elif manifest.merge_method == "dare_ties":
            return {
                "merge_method": "dare_ties",
                "base_model": base_model,
                "models": [
                    {"model": lora.adapter_path, "parameters": {"density": 0.5, "weight": 1.0}}
                    for lora in loras
                ],
                "dtype": "bfloat16",
                "parameters": {"normalize": True},
            }

        raise ValueError(f"Unknown merge method: {manifest.merge_method}")

    def _merge_with_mlx(
        self,
        manifest: MergeManifest,
        loras: list[AdapterManifest],
    ) -> MergeResult:
        """Merge LoRAs using MLX (Apple Silicon native)."""
        try:
            import mlx.core as mx
            from mlx_lm import load, save
        except ImportError:
            return MergeResult(
                success=False,
                error_message="MLX not available. Install with: pip install mlx mlx-lm",
            )

        # Prepare output directory
        models_dir = self.config.paths.models_dir
        output_dir = models_dir / "merged" / manifest.merge_id
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Load base model
            base_model, tokenizer = load(loras[0].base_model)
            base_weights = dict(base_model.parameters())

            # For each LoRA, load and apply
            for lora in loras:
                lora_weights = self._load_lora_weights(lora.adapter_path)
                base_weights = self._apply_lora_to_weights(
                    base_weights,
                    lora_weights,
                    scale=1.0 / len(loras),  # Equal contribution
                )

            # Update model with merged weights
            base_model.update(base_weights)

            # Save merged model
            save(str(output_dir), base_model, tokenizer)

            return MergeResult(
                success=True,
                output_path=str(output_dir),
            )

        except Exception as e:
            return MergeResult(
                success=False,
                error_message=f"MLX merge failed: {e}",
            )

    def _load_lora_weights(self, adapter_path: str) -> dict[str, Any]:
        """Load LoRA adapter weights from safetensors."""
        try:
            import mlx.core as mx
            from safetensors import safe_open
        except ImportError:
            raise RuntimeError("safetensors required for LoRA loading")

        weights: dict[str, Any] = {}
        adapter_file = Path(adapter_path) / "adapter.safetensors"

        with safe_open(str(adapter_file), framework="numpy") as f:
            for key in f.keys():
                weights[key] = mx.array(f.get_tensor(key))

        return weights

    def _apply_lora_to_weights(
        self,
        base: dict[str, Any],
        lora: dict[str, Any],
        scale: float,
    ) -> dict[str, Any]:
        """Apply LoRA weights to base model weights.

        LoRA applies a low-rank update: W' = W + scale * (B @ A)
        where A and B are the LoRA weight matrices.
        """
        try:
            import mlx.core as mx
        except ImportError:
            raise RuntimeError("MLX required for weight application")

        result = dict(base)

        # LoRA weights are typically named like:
        # layers.N.self_attn.q_proj.lora_A, layers.N.self_attn.q_proj.lora_B
        lora_a_keys = [k for k in lora if "lora_A" in k or "lora_a" in k]

        for a_key in lora_a_keys:
            b_key = a_key.replace("lora_A", "lora_B").replace("lora_a", "lora_b")
            if b_key not in lora:
                continue

            # Get the base weight key
            base_key = a_key.replace(".lora_A", "").replace(".lora_a", "")
            base_key = base_key.replace(".weight", "") + ".weight"

            if base_key not in result:
                continue

            # LoRA: W' = W + scale * (B @ A)
            a = lora[a_key]
            b = lora[b_key]
            delta = scale * (b @ a)

            result[base_key] = result[base_key] + delta

        return result
