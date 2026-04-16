from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from homunculus.config import load_config
from homunculus.dataset_builder.builder import DatasetBuilder
from homunculus.models import EvaluationMetrics
from homunculus.storage import ArtifactStore
from homunculus.trainer.manager import TrainingManager


class TrainerTests(unittest.TestCase):
    def _config_path(self, temp_dir: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        target = temp_dir / "config.toml"
        target.write_text(source.read_text(encoding="utf-8").replace('path = "."', f'path = "{temp_dir.as_posix()}"', 1), encoding="utf-8")
        return target

    def _seed_snapshot_inputs(self, config, store) -> None:
        seed_sample = {
            "messages": [{"role": "system", "content": "seed"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}],
            "episode_id": "seed-1",
            "source": "seed",
            "verification": {"diff_hash": "seed"},
        }
        config.paths.seed_sft_path.write_text(json.dumps(seed_sample) + "\n", encoding="utf-8")
        for split in ("train", "valid", "test"):
            store.append_jsonl(store.datasets_dir / "sft" / f"{split}.jsonl", seed_sample | {"episode_id": f"{split}-sample"})

    def test_should_train_sft_by_sample_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            builder = DatasetBuilder(config, store)
            trainer = TrainingManager(config, store, builder)
            self.assertTrue(trainer.should_train_sft(config.thresholds.train_after_samples, None))

    def test_simulated_training_registers_candidate_with_snapshot_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            config = load_config(self._config_path(temp_path))
            store = ArtifactStore(config)
            store.ensure_layout()
            builder = DatasetBuilder(config, store)
            trainer = TrainingManager(config, store, builder)
            self._seed_snapshot_inputs(config, store)
            manifest = trainer.run_sft(simulate=True)
            self.assertEqual(manifest.status, "trained")
            self.assertIn("--adapter-path", manifest.training_command)
            self.assertTrue(Path(manifest.snapshot_path or "").exists())
            self.assertIsNotNone(store.get_candidate(manifest.candidate_id or ""))

    def test_evaluate_then_promote_activates_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            config = load_config(self._config_path(temp_path))
            store = ArtifactStore(config)
            store.ensure_layout()
            builder = DatasetBuilder(config, store)
            trainer = TrainingManager(config, store, builder)
            self._seed_snapshot_inputs(config, store)
            candidate = trainer.run_sft(simulate=True)
            metrics = EvaluationMetrics(
                compile_pass_rate=1.0,
                task_success_rate=1.0,
                average_retries_to_success=0.0,
                regression_count=0,
                memory_usefulness_score=0.3,
                tool_misuse_rate=0.0,
            )
            evaluated = trainer.evaluate_candidate(candidate, metrics)
            self.assertEqual(evaluated.status, "evaluated")
            self.assertIsNone(store.active_candidate())
            # Now promotion should succeed without approval
            promoted = trainer.promote_candidate(evaluated)
            self.assertEqual(promoted.status, "promoted")
            self.assertIsNotNone(store.active_candidate())


class LineageWiringTests(unittest.TestCase):
    """promote_candidate must register the candidate in lineage so future
    merges can find its ancestry. Regression: register_lora was defined
    but never invoked from the training pipeline."""

    def _config_path(self, temp_dir: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        target = temp_dir / "config.toml"
        target.write_text(
            source.read_text(encoding="utf-8").replace(
                'path = "."', f'path = "{temp_dir.as_posix()}"', 1
            ),
            encoding="utf-8",
        )
        return target

    def _seed_snapshot_inputs(self, config, store) -> None:
        seed_sample = {
            "messages": [
                {"role": "system", "content": "seed"},
                {"role": "user", "content": "u"},
                {"role": "assistant", "content": "a"},
            ],
            "episode_id": "seed-1",
            "source": "seed",
            "verification": {"diff_hash": "seed"},
        }
        config.paths.seed_sft_path.write_text(
            json.dumps(seed_sample) + "\n", encoding="utf-8"
        )
        for split in ("train", "valid", "test"):
            store.append_jsonl(
                store.datasets_dir / "sft" / f"{split}.jsonl",
                seed_sample | {"episode_id": f"{split}-sample"},
            )

    def _passing_metrics(self) -> EvaluationMetrics:
        return EvaluationMetrics(
            compile_pass_rate=1.0,
            task_success_rate=1.0,
            average_retries_to_success=0.0,
            regression_count=0,
            memory_usefulness_score=0.3,
            tool_misuse_rate=0.0,
        )

    def test_promote_candidate_registers_lora_in_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            config = load_config(self._config_path(temp_path))
            store = ArtifactStore(config)
            store.ensure_layout()
            builder = DatasetBuilder(config, store)
            trainer = TrainingManager(config, store, builder)
            self._seed_snapshot_inputs(config, store)

            candidate = trainer.run_sft(simulate=True)
            # Sanity: run_sft populated contributing_episode_ids from snapshot
            self.assertGreater(
                len(candidate.contributing_episode_ids),
                0,
                "run_sft must populate contributing_episode_ids from snapshot",
            )
            evaluated = trainer.evaluate_candidate(candidate, self._passing_metrics())
            promoted = trainer.promote_candidate(evaluated)

            # Inspect lineage: a LoRA record for this candidate must exist
            records = store.load_lineage()
            lora_records = [r for r in records if r.record_type == "lora"]
            matching = [r for r in lora_records if r.record_id == promoted.candidate_id]
            self.assertEqual(
                len(matching),
                1,
                f"Expected exactly one LoRA lineage record for "
                f"{promoted.candidate_id!r}; got: {[r.record_id for r in lora_records]}",
            )
            cand_record = matching[0]
            # Episode ids should match what was in the candidate
            self.assertEqual(
                set(cand_record.episode_ids),
                set(promoted.contributing_episode_ids),
            )
            # Base model should be registered as parent
            self.assertEqual(len(cand_record.parent_ids), 1)
            self.assertTrue(cand_record.parent_ids[0].startswith("base-"))

    def test_promote_failure_does_not_register_lineage(self) -> None:
        """If promotion gates reject the candidate, no lineage record is created."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            config = load_config(self._config_path(temp_path))
            store = ArtifactStore(config)
            store.ensure_layout()
            builder = DatasetBuilder(config, store)
            trainer = TrainingManager(config, store, builder)
            self._seed_snapshot_inputs(config, store)

            candidate = trainer.run_sft(simulate=True)
            failing_metrics = EvaluationMetrics(
                compile_pass_rate=0.0,
                task_success_rate=0.0,
                average_retries_to_success=999.0,
                regression_count=5,
                memory_usefulness_score=0.0,
                tool_misuse_rate=1.0,
            )
            evaluated = trainer.evaluate_candidate(candidate, failing_metrics)
            with self.assertRaises(RuntimeError):
                trainer.promote_candidate(evaluated)

            # No LoRA record should be created on rejection
            records = store.load_lineage()
            lora_records = [
                r for r in records
                if r.record_type == "lora" and r.record_id == candidate.candidate_id
            ]
            self.assertEqual(
                lora_records,
                [],
                "Rejected candidate must not appear in lineage",
            )


if __name__ == "__main__":
    unittest.main()
