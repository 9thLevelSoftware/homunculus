"""LoRA merge pipeline for weight evolution."""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..config import HomunculusConfig
from ..models import AdapterManifest, MergeManifest, utc_now
from ..storage import ArtifactStore

logger = logging.getLogger(__name__)


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

        bases = {lora.base_model for lora in loras if lora.base_model}
        if len(bases) > 1:
            raise ValueError(
                f"All source LoRAs must share the same base model; "
                f"got: {sorted(bases)}"
            )
        if not bases:
            raise ValueError("No source LoRAs have a base_model set")
        target_base = bases.pop()

        manifest = MergeManifest(
            merge_id=merge_id,
            source_loras=[lora.candidate_id for lora in loras if lora.candidate_id],
            target_base=target_base,
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

        # Initialize before try so the finally can run safely even if
        # config generation or temp-file creation raises.
        config_path: str | None = None
        try:
            # Generate mergekit config YAML
            config = self._generate_mergekit_config(manifest, loras)

            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                yaml.safe_dump(config, f)
                config_path = f.name

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
            if config_path is not None:
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
        """Merge LoRAs into a base model via MLX (Apple Silicon native).

        Three fixes vs. the original:
        1. Uses ``mlx_lm.utils.save_weights`` + manual tokenizer/config persistence
           instead of the non-existent ``mlx_lm.save`` symbol.
        2. Reads each LoRA's ``adapter_config.json`` for ``alpha``/``r`` so the
           PEFT scaling ``(alpha/r) * (B @ A)`` is applied correctly. The
           previous implementation under-scaled by ~2-4x.
        3. Any per-LoRA apply that matches zero base keys raises
           ``RuntimeError`` (caught here and surfaced as a failed merge)
           rather than silently producing the unchanged base model.
        """
        try:
            from mlx_lm.utils import load, save_weights
        except ImportError:
            return MergeResult(
                success=False,
                error_message="MLX not available. Install with: pip install mlx mlx-lm",
            )

        models_dir = self.config.paths.models_dir
        output_dir = models_dir / "merged" / manifest.merge_id
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            base_model, tokenizer = load(loras[0].base_model)
            base_weights = dict(base_model.parameters())

            for lora in loras:
                lora_weights = self._load_lora_weights(lora.adapter_path)
                alpha, rank = self._read_lora_config(lora.adapter_path)
                base_weights = self._apply_lora_to_weights(
                    base_weights,
                    lora_weights,
                    scale=1.0 / len(loras),  # average across LoRAs
                    alpha=alpha,
                    rank=rank,
                )

            base_model.update(base_weights)

            save_weights(str(output_dir / "weights.safetensors"), base_weights)
            try:
                tokenizer.save_pretrained(str(output_dir))
            except Exception as exc:
                logger.warning("Failed to save tokenizer for %s: %s",
                               manifest.merge_id, exc)
            src_config = Path(loras[0].base_model) / "config.json"
            if src_config.exists():
                import shutil
                shutil.copy(src_config, output_dir / "config.json")

            return MergeResult(
                success=True,
                output_path=str(output_dir),
            )

        except Exception as e:
            logger.exception("MLX merge failed for %s", manifest.merge_id)
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

    def _read_lora_config(self, adapter_path: str) -> tuple[int, int]:
        """Read ``lora_alpha`` and ``r`` from PEFT's adapter_config.json.

        Falls back to PEFT's own defaults (alpha=16, r=8) when the file
        is missing or unreadable. Logs a warning in the fallback case so
        the operator sees a merge may be under-scaled.
        """
        cfg_path = Path(adapter_path) / "adapter_config.json"
        if not cfg_path.exists():
            logger.warning(
                "No adapter_config.json at %s; using defaults alpha=16, r=8",
                adapter_path,
            )
            return 16, 8
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to read adapter_config.json at %s (%s); using defaults",
                adapter_path, exc,
            )
            return 16, 8
        alpha = int(cfg.get("lora_alpha", 16))
        rank = int(cfg.get("r", 8))
        return alpha, rank

    def _apply_lora_to_weights(
        self,
        base: dict[str, Any],
        lora: dict[str, Any],
        *,
        scale: float,
        alpha: int,
        rank: int,
    ) -> dict[str, Any]:
        """Apply a PEFT LoRA adapter's delta to a base weight dict.

        The PEFT delta is ``(alpha / r) * (B @ A)``. ``scale`` further
        averages across multiple LoRAs (typically ``1 / len(loras)``).

        PEFT stores adapter keys as::

            base_model.model.<module-path>.lora_A.weight
            base_model.model.<module-path>.lora_B.weight

        The MLX-loaded base exposes ``<module-path>.weight``. This helper
        strips PEFT's ``base_model.model.`` prefix and the ``lora_A``/
        ``lora_a`` suffix variants before comparison.

        Raises:
            RuntimeError: If the LoRA matches zero base keys. This is the
                indicator for the silent-no-op bug we ship-regressed against
                — a merge that applies no deltas is either a key-mismatch
                bug or a PEFT/MLX version drift we want to notice loudly.
        """
        result = dict(base)
        if rank <= 0:
            raise ValueError(f"LoRA rank must be positive, got {rank}")
        lora_scale = (alpha / rank) * scale
        applied = 0

        a_keys = [
            k for k in lora
            if k.endswith(".lora_A.weight") or k.endswith(".lora_a.weight")
        ]

        for a_key in a_keys:
            b_key = (
                a_key
                .replace(".lora_A.weight", ".lora_B.weight")
                .replace(".lora_a.weight", ".lora_b.weight")
            )
            if b_key not in lora:
                continue

            base_key = a_key
            if base_key.startswith("base_model.model."):
                base_key = base_key[len("base_model.model."):]
            base_key = (
                base_key
                .replace(".lora_A.weight", ".weight")
                .replace(".lora_a.weight", ".weight")
            )
            if base_key not in result:
                continue

            a = lora[a_key]
            b = lora[b_key]
            delta = lora_scale * (b @ a)
            result[base_key] = result[base_key] + delta
            applied += 1

        if applied == 0:
            lora_sample = list(lora)[:3]
            base_sample = list(base)[:3]
            raise RuntimeError(
                f"zero deltas applied — LoRA/base key mismatch. "
                f"LoRA keys sample: {lora_sample}; base keys sample: {base_sample}. "
                f"Check PEFT version and base-model naming convention."
            )
        logger.info(
            "Applied %d LoRA deltas (alpha=%d, r=%d, scale=%g)",
            applied, alpha, rank, scale,
        )
        return result
