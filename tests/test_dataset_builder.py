from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from homunculus.config import load_config
from homunculus.dataset_builder.builder import DatasetBuilder
from homunculus.models import EpisodeRecord, VerificationResult
from homunculus.storage import ArtifactStore


class DatasetBuilderTests(unittest.TestCase):
    def _config_path(self, temp_dir: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        target = temp_dir / "config.toml"
        target.write_text(source.read_text(encoding="utf-8").replace('path = "."', f'path = "{temp_dir.as_posix()}"', 1), encoding="utf-8")
        return target

    def test_ingest_episode_adds_sft_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()
            builder = DatasetBuilder(config, store)
            episode = EpisodeRecord(
                episode_id="episode-1",
                task_id="task-1",
                workspace="self",
                prompt="Fix bug",
                plan=["inspect", "patch"],
                teacher_output={"rationale": "safe"},
                student_output={"text": "hint"},
                diff_hash="abc123",
                test_results=[VerificationResult(name="unit", command="python -m unittest", passed=True)],
                lint_results=[],
                outcome="accepted",
                timestamp="2026-04-14T00:00:00+00:00",
                patch="diff --git a/x b/x",
                patch_path="traces/patches/episode-1.patch",
                verification_passed=True,
            )
            result = builder.ingest_episode(episode)
            self.assertEqual(result["sft_added"], 1)
            result = builder.ingest_episode(episode)
            self.assertEqual(result["sft_added"], 0)

    def test_build_preference_pair_uses_episode_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()
            builder = DatasetBuilder(config, store)
            loser = EpisodeRecord(
                episode_id="episode-loser",
                task_id="task-2",
                workspace="self",
                prompt="Refactor module",
                plan=["bad-plan"],
                teacher_output={"rationale": "bad"},
                student_output={},
                diff_hash="loser",
                test_results=[VerificationResult(name="unit", command="python", passed=False)],
                lint_results=[],
                outcome="reverted",
                timestamp="2026-04-14T00:00:00+00:00",
                patch="bad patch",
                patch_path="traces/patches/episode-loser.patch",
                comparison_group="group-a",
                verification_passed=False,
                review_status="needs_review",
            )
            winner = EpisodeRecord(
                episode_id="episode-winner",
                task_id="task-2",
                workspace="self",
                prompt="Refactor module",
                plan=["good-plan"],
                teacher_output={"rationale": "good"},
                student_output={},
                diff_hash="winner",
                test_results=[VerificationResult(name="unit", command="python", passed=True)],
                lint_results=[],
                outcome="accepted",
                timestamp="2026-04-14T00:01:00+00:00",
                patch="good patch",
                patch_path="traces/patches/episode-winner.patch",
                comparison_group="group-a",
                verification_passed=True,
            )
            store.append_episode(loser)
            store.append_episode(winner)
            pair = builder.build_preference_pair(winner)
            self.assertIsNotNone(pair)
            self.assertEqual(pair.episode_ids, ["episode-winner", "episode-loser"])

    def test_materialize_snapshot_writes_metadata_and_splits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            config = load_config(self._config_path(temp_path))
            store = ArtifactStore(config)
            store.ensure_layout()
            builder = DatasetBuilder(config, store)
            seed_sample = {
                "messages": [{"role": "system", "content": "seed"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}],
                "episode_id": "seed-1",
                "source": "seed",
                "verification": {"diff_hash": "seed"},
            }
            config.paths.seed_sft_path.write_text(json.dumps(seed_sample) + "\n", encoding="utf-8")
            for split in ("train", "valid", "test"):
                store.append_jsonl(
                    store.datasets_dir / "sft" / f"{split}.jsonl",
                    seed_sample | {"episode_id": f"{split}-sample", "verification": {"diff_hash": split}},
                )
            snapshot = builder.materialize_sft_snapshot()
            snapshot_dir = Path(snapshot.snapshot_path)
            self.assertTrue((snapshot_dir / "train.jsonl").exists())
            self.assertTrue((snapshot_dir / "valid.jsonl").exists())
            self.assertTrue((snapshot_dir / "test.jsonl").exists())
            metadata = json.loads((snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["snapshot_id"], snapshot.snapshot_id)
            self.assertEqual(metadata["selected_episode_ids"]["train"], ["train-sample"])


if __name__ == "__main__":
    unittest.main()
