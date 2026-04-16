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

    def test_evaluate_does_not_activate_and_promote_requires_approval(self) -> None:
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
            with self.assertRaises(RuntimeError):
                trainer.promote_candidate(evaluated, human_approved=False)


if __name__ == "__main__":
    unittest.main()
