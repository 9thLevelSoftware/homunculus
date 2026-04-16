import json
import tempfile
import unittest
from pathlib import Path

from homunculus.config import EvolutionSettings, HomunculusConfig, load_config
from homunculus.models import LineageRecord, MergeManifest


class EvolutionInfrastructureTests(unittest.TestCase):
    def test_evolution_settings_defaults(self):
        settings = EvolutionSettings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.merge_after_loras, 3)
        self.assertEqual(settings.max_merge_attempts, 3)
        self.assertEqual(settings.merge_backend, "auto")
        self.assertEqual(settings.validation_timeout_seconds, 300)
        self.assertEqual(settings.coherence_prompt, "Explain what you are and what you do.")
        self.assertEqual(settings.coherence_min_tokens, 50)

    def test_merge_manifest_serialization(self):
        manifest = MergeManifest(
            merge_id="merge-001",
            source_loras=["lora-a", "lora-b"],
            target_base="qwen2.5-coder-1.5b",
            merge_method="linear",
        )
        data = manifest.to_dict()
        restored = MergeManifest.from_dict(data)
        self.assertEqual(restored.merge_id, "merge-001")
        self.assertEqual(restored.source_loras, ["lora-a", "lora-b"])
        self.assertEqual(restored.status, "pending")
        self.assertEqual(restored.target_base, "qwen2.5-coder-1.5b")
        self.assertEqual(restored.merge_method, "linear")
        self.assertIsNone(restored.completed_at)
        self.assertIsNone(restored.output_path)
        self.assertIsNone(restored.error_message)
        self.assertEqual(restored.merge_params, {})
        self.assertEqual(restored.validation_results, {})

    def test_merge_manifest_with_all_fields(self):
        manifest = MergeManifest(
            merge_id="merge-002",
            source_loras=["lora-x", "lora-y", "lora-z"],
            target_base="model-v2",
            merge_method="ties",
            merge_params={"weight": 0.5},
            status="complete",
            completed_at="2026-04-16T12:00:00+00:00",
            output_path="/models/merged",
            validation_results={"coherence": True},
            error_message=None,
        )
        data = manifest.to_dict()
        restored = MergeManifest.from_dict(data)
        self.assertEqual(restored.status, "complete")
        self.assertEqual(restored.merge_params, {"weight": 0.5})
        self.assertEqual(restored.output_path, "/models/merged")

    def test_lineage_record_serialization(self):
        record = LineageRecord(
            record_id="gen-1",
            record_type="merged",
            model_id="qwen2.5-coder-1.5b-gen1",
            parent_ids=["base", "lora-001"],
            generation=1,
        )
        data = record.to_dict()
        restored = LineageRecord.from_dict(data)
        self.assertEqual(restored.record_id, "gen-1")
        self.assertEqual(restored.generation, 1)
        self.assertEqual(len(restored.parent_ids), 2)
        self.assertEqual(restored.record_type, "merged")
        self.assertEqual(restored.model_id, "qwen2.5-coder-1.5b-gen1")
        self.assertEqual(restored.episode_ids, [])
        self.assertIsNone(restored.merge_id)
        self.assertEqual(restored.metadata, {})

    def test_lineage_record_with_all_fields(self):
        record = LineageRecord(
            record_id="lora-001",
            record_type="lora",
            model_id="adapter-v1",
            parent_ids=["base-model"],
            episode_ids=["ep-1", "ep-2", "ep-3"],
            merge_id=None,
            generation=0,
            metadata={"training_samples": 100},
        )
        data = record.to_dict()
        restored = LineageRecord.from_dict(data)
        self.assertEqual(restored.episode_ids, ["ep-1", "ep-2", "ep-3"])
        self.assertEqual(restored.metadata, {"training_samples": 100})


class EvolutionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Create minimal config structure
        self.traces_dir = self.temp_path / "traces"
        self.traces_dir.mkdir(parents=True)
        self.datasets_dir = self.temp_path / "datasets"
        self.datasets_dir.mkdir(parents=True)
        self.models_dir = self.temp_path / "models"
        self.models_dir.mkdir(parents=True)
        self.runtime_dir = self.temp_path / "runtime"
        self.runtime_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_store(self):
        """Create an ArtifactStore with mocked config paths."""
        from homunculus.storage import ArtifactStore
        from homunculus.config import PathSettings

        # Create a minimal mock config
        class MockConfig:
            def __init__(self, paths):
                self.paths = paths

        paths = PathSettings(
            root=self.temp_path,
            traces_dir=self.traces_dir,
            datasets_dir=self.datasets_dir,
            models_dir=self.models_dir,
            runtime_dir=self.runtime_dir,
            seed_sft_path=self.datasets_dir / "seed" / "sft_seed.jsonl",
            seed_dpo_path=self.datasets_dir / "seed" / "dpo_seed.jsonl",
        )
        return ArtifactStore(MockConfig(paths))

    def test_merges_path(self):
        store = self._make_store()
        self.assertEqual(store.merges_path(), self.traces_dir / "merges.jsonl")

    def test_lineage_path(self):
        store = self._make_store()
        self.assertEqual(store.lineage_path(), self.traces_dir / "lineage.jsonl")

    def test_append_and_load_merge(self):
        store = self._make_store()
        manifest = MergeManifest(
            merge_id="merge-test",
            source_loras=["a", "b"],
            target_base="base-model",
            merge_method="linear",
        )
        store.append_merge(manifest)

        merges = store.load_merges()
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0].merge_id, "merge-test")

    def test_get_merge(self):
        store = self._make_store()
        manifest1 = MergeManifest(
            merge_id="merge-1",
            source_loras=["a"],
            target_base="base",
            merge_method="linear",
        )
        manifest2 = MergeManifest(
            merge_id="merge-2",
            source_loras=["b"],
            target_base="base",
            merge_method="ties",
        )
        store.append_merge(manifest1)
        store.append_merge(manifest2)

        result = store.get_merge("merge-2")
        self.assertIsNotNone(result)
        self.assertEqual(result.merge_id, "merge-2")
        self.assertEqual(result.merge_method, "ties")

        result_none = store.get_merge("nonexistent")
        self.assertIsNone(result_none)

    def test_update_merge(self):
        store = self._make_store()
        manifest = MergeManifest(
            merge_id="merge-update",
            source_loras=["a", "b"],
            target_base="base",
            merge_method="linear",
            status="pending",
        )
        store.append_merge(manifest)

        # Update status
        manifest.status = "complete"
        manifest.completed_at = "2026-04-16T12:00:00+00:00"
        store.update_merge(manifest)

        result = store.get_merge("merge-update")
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.completed_at, "2026-04-16T12:00:00+00:00")

    def test_append_and_load_lineage(self):
        store = self._make_store()
        record = LineageRecord(
            record_id="lineage-test",
            record_type="base",
            model_id="base-model",
            generation=0,
        )
        store.append_lineage(record)

        records = store.load_lineage()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].record_id, "lineage-test")

    def test_get_lineage_record(self):
        store = self._make_store()
        record1 = LineageRecord(
            record_id="rec-1",
            record_type="base",
            model_id="model-1",
            generation=0,
        )
        record2 = LineageRecord(
            record_id="rec-2",
            record_type="lora",
            model_id="model-2",
            generation=0,
        )
        store.append_lineage(record1)
        store.append_lineage(record2)

        result = store.get_lineage_record("rec-2")
        self.assertIsNotNone(result)
        self.assertEqual(result.record_type, "lora")

        result_none = store.get_lineage_record("nonexistent")
        self.assertIsNone(result_none)

    def test_get_lineage_by_generation(self):
        store = self._make_store()
        records = [
            LineageRecord(record_id="base", record_type="base", model_id="m0", generation=0),
            LineageRecord(record_id="lora-1", record_type="lora", model_id="m1", generation=0),
            LineageRecord(record_id="merged-1", record_type="merged", model_id="m2", generation=1),
            LineageRecord(record_id="lora-2", record_type="lora", model_id="m3", generation=1),
            LineageRecord(record_id="merged-2", record_type="merged", model_id="m4", generation=2),
        ]
        for r in records:
            store.append_lineage(r)

        gen0 = store.get_lineage_by_generation(0)
        self.assertEqual(len(gen0), 2)
        self.assertEqual({r.record_id for r in gen0}, {"base", "lora-1"})

        gen1 = store.get_lineage_by_generation(1)
        self.assertEqual(len(gen1), 2)
        self.assertEqual({r.record_id for r in gen1}, {"merged-1", "lora-2"})

        gen2 = store.get_lineage_by_generation(2)
        self.assertEqual(len(gen2), 1)
        self.assertEqual(gen2[0].record_id, "merged-2")


if __name__ == "__main__":
    unittest.main()
