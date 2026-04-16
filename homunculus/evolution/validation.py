"""Merge validation pipeline for weight evolution."""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import HomunculusConfig
from ..models import MergeManifest

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a single validation stage."""

    stage: str  # "load" | "canary" | "coherence"
    passed: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class FullValidationResult:
    """Complete validation result across all stages."""

    passed: bool
    stages: list[ValidationResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "stages": [
                {"stage": s.stage, "passed": s.passed, "message": s.message, "details": s.details}
                for s in self.stages
            ],
        }


class MergeValidator:
    """Validates merged models before adoption.

    Runs a 3-stage validation pipeline:
    1. Load test - verify weights parse correctly
    2. Canary suite - run verification commands
    3. Coherence check - model generates sensible output
    """

    def __init__(self, config: HomunculusConfig) -> None:
        self.config = config

    def validate(self, manifest: MergeManifest) -> FullValidationResult:
        """Run full 3-stage validation on a merged model.

        Stages:
        1. Load test - verify weights parse correctly
        2. Canary suite - run verification commands
        3. Coherence check - model generates sensible output

        Returns:
            FullValidationResult with pass/fail for each stage
        """
        stages: list[ValidationResult] = []

        # Stage 1: Load test
        load_result = self._validate_load(manifest)
        stages.append(load_result)
        if not load_result.passed:
            return FullValidationResult(passed=False, stages=stages)

        # Stage 2: Canary suite
        canary_result = self._validate_canary(manifest)
        stages.append(canary_result)
        if not canary_result.passed:
            return FullValidationResult(passed=False, stages=stages)

        # Stage 3: Coherence check
        coherence_result = self._validate_coherence(manifest)
        stages.append(coherence_result)

        return FullValidationResult(
            passed=coherence_result.passed,
            stages=stages,
        )

    def _validate_load(self, manifest: MergeManifest) -> ValidationResult:
        """Stage 1: Verify model weights load without corruption."""
        if not manifest.output_path:
            return ValidationResult(
                stage="load",
                passed=False,
                message="No output path specified",
            )

        output_dir = Path(manifest.output_path)
        if not output_dir.exists():
            return ValidationResult(
                stage="load",
                passed=False,
                message=f"Output directory does not exist: {output_dir}",
            )

        # Check for expected model files
        weight_patterns = ["*.safetensors", "*.bin", "pytorch_model*.bin", "model*.safetensors"]

        # Verify config exists
        if not (output_dir / "config.json").exists():
            return ValidationResult(
                stage="load",
                passed=False,
                message="config.json not found",
            )

        # Verify at least one weight file exists
        weight_files = []
        for pattern in weight_patterns:
            weight_files.extend(output_dir.glob(pattern))

        if not weight_files:
            return ValidationResult(
                stage="load",
                passed=False,
                message="No weight files found (safetensors or bin)",
            )

        # Try to actually load the model (basic parsing check)
        try:
            # Use safetensors to verify file integrity if available
            from safetensors import safe_open

            for wf in weight_files:
                if wf.suffix == ".safetensors":
                    with safe_open(str(wf), framework="pt") as f:
                        # Just verify we can list keys
                        keys = list(f.keys())
                        if not keys:
                            return ValidationResult(
                                stage="load",
                                passed=False,
                                message=f"Empty weight file: {wf.name}",
                            )
        except ImportError:
            # safetensors not available, skip deep validation
            pass
        except Exception as e:
            return ValidationResult(
                stage="load",
                passed=False,
                message=f"Failed to parse weights: {e}",
            )

        return ValidationResult(
            stage="load",
            passed=True,
            message="Model files present and parseable",
            details={"weight_files": [str(f.name) for f in weight_files]},
        )

    def _validate_canary(self, manifest: MergeManifest) -> ValidationResult:
        """Stage 2: Run canary commands against the merged model."""
        # Check if canary_commands exists on config
        canary_commands = getattr(self.config, "canary_commands", None)

        if not canary_commands:
            return ValidationResult(
                stage="canary",
                passed=True,
                message="No canary commands configured (skipped)",
            )

        failed_canaries = []
        passed_canaries = []

        for canary in canary_commands:
            try:
                # Substitute model path in command if needed
                cmd = canary.command.replace("{model_path}", manifest.output_path or "")

                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self.config.evolution.validation_timeout_seconds,
                    cwd=str(self.config.paths.root) if hasattr(self.config.paths, "root") else None,
                )

                if result.returncode == 0:
                    passed_canaries.append(canary.name)
                else:
                    failed_canaries.append({
                        "name": canary.name,
                        "returncode": result.returncode,
                        "stderr": result.stderr[:500],  # Truncate for storage
                    })

            except subprocess.TimeoutExpired:
                failed_canaries.append({
                    "name": canary.name,
                    "error": "timeout",
                })
            except Exception as e:
                failed_canaries.append({
                    "name": canary.name,
                    "error": str(e),
                })

        if failed_canaries:
            return ValidationResult(
                stage="canary",
                passed=False,
                message=f"{len(failed_canaries)} canary command(s) failed",
                details={"failed": failed_canaries, "passed": passed_canaries},
            )

        return ValidationResult(
            stage="canary",
            passed=True,
            message=f"All {len(passed_canaries)} canary commands passed",
            details={"passed": passed_canaries},
        )

    def _validate_coherence(self, manifest: MergeManifest) -> ValidationResult:
        """Stage 3: Verify model generates coherent output."""
        if not manifest.output_path:
            return ValidationResult(
                stage="coherence",
                passed=False,
                message="No output path for coherence check",
            )

        prompt = self.config.evolution.coherence_prompt
        min_tokens = self.config.evolution.coherence_min_tokens

        import platform

        # Platform-specific generation (skip MLX on non-Darwin)
        output = None
        if platform.system() == "Darwin":
            try:
                output = self._generate_mlx(manifest.output_path, prompt)
            except ImportError:
                logger.info("MLX not installed; falling through to transformers")
            except Exception as e:
                logger.warning(
                    "MLX generation failed: %s; falling through to transformers", e
                )

        if output is None:
            try:
                output = self._generate_transformers(manifest.output_path, prompt)
            except ImportError:
                # Fail closed: no inference backend means we cannot verify the merge
                return ValidationResult(
                    stage="coherence",
                    passed=False,
                    message="backend_unavailable: install mlx_lm or transformers to enable evolution",
                )
            except Exception as e:
                return ValidationResult(
                    stage="coherence",
                    passed=False,
                    message=f"Failed to generate: {e}",
                )

        # Check output quality
        if not output or len(output.strip()) == 0:
            return ValidationResult(
                stage="coherence",
                passed=False,
                message="Model produced empty output",
            )

        # Token count approximation (words * 1.3)
        approx_tokens = len(output.split()) * 1.3
        if approx_tokens < min_tokens:
            return ValidationResult(
                stage="coherence",
                passed=False,
                message=f"Output too short: ~{int(approx_tokens)} tokens < {min_tokens} required",
                details={"output_preview": output[:200]},
            )

        # Basic coherence checks
        if self._is_repetitive(output):
            return ValidationResult(
                stage="coherence",
                passed=False,
                message="Output is repetitive (possible degeneration)",
                details={"output_preview": output[:200]},
            )

        return ValidationResult(
            stage="coherence",
            passed=True,
            message="Model generates coherent output",
            details={"output_preview": output[:200], "approx_tokens": int(approx_tokens)},
        )

    def _generate_mlx(self, model_path: str, prompt: str) -> str:
        """Generate using MLX (Apple Silicon)."""
        from mlx_lm import generate, load

        model, tokenizer = load(model_path)
        output = generate(model, tokenizer, prompt=prompt, max_tokens=200)
        return output

    def _generate_transformers(self, model_path: str, prompt: str) -> str:
        """Generate using transformers (fallback)."""
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_path)
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype=torch.bfloat16,
            device_map="auto",
        )

        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        outputs = model.generate(**inputs, max_new_tokens=200, do_sample=True, temperature=0.7)
        return tokenizer.decode(outputs[0], skip_special_tokens=True)

    def _is_repetitive(self, text: str, threshold: float = 0.5) -> bool:
        """Check if text is repetitively degenerate."""
        words = text.lower().split()
        if len(words) < 10:
            return False

        # Check for repeated n-grams
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        bigram_counts = Counter(bigrams)

        if not bigram_counts:
            return False

        most_common_count = bigram_counts.most_common(1)[0][1]
        repetition_ratio = most_common_count / len(bigrams)

        return repetition_ratio > threshold
