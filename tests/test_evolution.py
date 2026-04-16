import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from homunculus.config import EvolutionSettings, HomunculusConfig, load_config
from homunculus.models import AdapterManifest, LineageRecord, MergeManifest


def _has_inference_backend() -> bool:
    """Check if any inference backend (MLX or transformers) is importable."""
    try:
        import mlx_lm  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


class EvolutionInfrastructureTests(unittest.TestCase):
    def test_evolution_settings_defaults(self):
        settings = EvolutionSettings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.auto_merge_after_loras, 5)
        self.assertEqual(settings.max_merge_attempts, 3)
        self.assertEqual(settings.merge_backend, "auto")
        self.assertEqual(settings.validation_timeout_seconds, 300)
        self.assertEqual(
            settings.coherence_prompt,
            "Write a Python function that returns the nth Fibonacci number.",
        )
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


class MergeManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Create mock config
        self.config = MagicMock()
        self.config.evolution.merge_backend = "auto"
        self.config.evolution.auto_merge_after_loras = 3
        self.config.evolution.validation_timeout_seconds = 300
        self.config.paths.models_dir = self.temp_path / "models"
        self.config.paths.models_dir.mkdir(parents=True, exist_ok=True)

        # Create mock store
        self.store = MagicMock()
        self.store.load_registry.return_value = {"candidates": [], "history": []}
        self.store.load_merges.return_value = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_backend_returns_valid_value(self):
        from homunculus.evolution.merge import detect_backend

        backend = detect_backend()
        self.assertIn(backend, ["mergekit", "mlx"])

    def test_should_merge_false_when_no_promoted(self):
        from homunculus.evolution.merge import MergeManager

        mgr = MergeManager(self.config, self.store)
        self.assertFalse(mgr.should_merge())

    def test_should_merge_true_when_enough_promoted(self):
        from homunculus.evolution.merge import MergeManager

        self.store.load_registry.return_value = {
            "candidates": [
                {"candidate_id": "a", "status": "promoted", "created_at": "2024-01-01"},
                {"candidate_id": "b", "status": "promoted", "created_at": "2024-01-02"},
                {"candidate_id": "c", "status": "promoted", "created_at": "2024-01-03"},
            ]
        }
        mgr = MergeManager(self.config, self.store)
        self.assertTrue(mgr.should_merge())

    def test_should_merge_respects_threshold(self):
        from homunculus.evolution.merge import MergeManager

        # Only 2 promoted, threshold is 3
        self.store.load_registry.return_value = {
            "candidates": [
                {"candidate_id": "a", "status": "promoted", "created_at": "2024-01-01"},
                {"candidate_id": "b", "status": "promoted", "created_at": "2024-01-02"},
            ]
        }
        mgr = MergeManager(self.config, self.store)
        self.assertFalse(mgr.should_merge())

    def test_should_merge_excludes_non_promoted(self):
        from homunculus.evolution.merge import MergeManager

        self.store.load_registry.return_value = {
            "candidates": [
                {"candidate_id": "a", "status": "promoted", "created_at": "2024-01-01"},
                {"candidate_id": "b", "status": "promoted", "created_at": "2024-01-02"},
                {"candidate_id": "c", "status": "pending", "created_at": "2024-01-03"},
                {"candidate_id": "d", "status": "rejected", "created_at": "2024-01-04"},
            ]
        }
        mgr = MergeManager(self.config, self.store)
        # Only 2 promoted, threshold is 3
        self.assertFalse(mgr.should_merge())

    def test_get_merge_candidates_excludes_pre_merge(self):
        from homunculus.evolution.merge import MergeManager

        self.store.load_registry.return_value = {
            "candidates": [
                {
                    "candidate_id": "old",
                    "status": "promoted",
                    "created_at": "2024-01-01T00:00:00",
                    "model_id": "m",
                    "base_model": "b",
                    "adapter_path": "/p",
                    "dataset_snapshot": "s",
                    "snapshot_path": None,
                    "trainer": "t",
                    "metrics": {},
                },
                {
                    "candidate_id": "new",
                    "status": "promoted",
                    "created_at": "2024-01-10T00:00:00",
                    "model_id": "m",
                    "base_model": "b",
                    "adapter_path": "/p",
                    "dataset_snapshot": "s",
                    "snapshot_path": None,
                    "trainer": "t",
                    "metrics": {},
                },
            ]
        }
        # Simulate a merge happened on 2024-01-05
        self.store.load_merges.return_value = [
            MergeManifest(
                merge_id="m1",
                source_loras=["old"],
                target_base="model",
                merge_method="linear",
                created_at="2024-01-05T00:00:00",
            )
        ]

        mgr = MergeManager(self.config, self.store)
        candidates = mgr.get_merge_candidates()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].candidate_id, "new")

    def test_get_merge_candidates_returns_all_when_no_merges(self):
        from homunculus.evolution.merge import MergeManager

        self.store.load_registry.return_value = {
            "candidates": [
                {
                    "candidate_id": "a",
                    "status": "promoted",
                    "created_at": "2024-01-01T00:00:00",
                    "model_id": "m",
                    "base_model": "b",
                    "adapter_path": "/p",
                    "dataset_snapshot": "s",
                    "snapshot_path": None,
                    "trainer": "t",
                    "metrics": {},
                },
                {
                    "candidate_id": "b",
                    "status": "promoted",
                    "created_at": "2024-01-02T00:00:00",
                    "model_id": "m",
                    "base_model": "b",
                    "adapter_path": "/p",
                    "dataset_snapshot": "s",
                    "snapshot_path": None,
                    "trainer": "t",
                    "metrics": {},
                },
            ]
        }
        # No merges
        self.store.load_merges.return_value = []

        mgr = MergeManager(self.config, self.store)
        candidates = mgr.get_merge_candidates()

        self.assertEqual(len(candidates), 2)

    def test_merge_creates_manifest(self):
        from homunculus.evolution.merge import MergeManager, MergeResult

        loras = [
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path=str(self.temp_path / "lora1"),
                dataset_snapshot="snap1",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-01",
                candidate_id="lora1",
            )
        ]

        # Mock the actual merge to avoid needing real backends
        mgr = MergeManager(self.config, self.store)
        mgr._merge_with_mergekit = MagicMock(
            return_value=MergeResult(success=True, output_path="/out")
        )
        mgr._merge_with_mlx = MagicMock(
            return_value=MergeResult(success=True, output_path="/out")
        )

        result = mgr.merge(loras)

        # Verify manifest was created
        self.store.append_merge.assert_called_once()
        manifest = self.store.append_merge.call_args[0][0]
        self.assertEqual(manifest.source_loras, ["lora1"])
        self.assertEqual(manifest.merge_method, "linear")

    def test_merge_with_empty_loras_fails(self):
        from homunculus.evolution.merge import MergeManager

        mgr = MergeManager(self.config, self.store)
        result = mgr.merge([])

        self.assertFalse(result.success)
        self.assertIn("No LoRAs", result.error_message)

    def test_merge_updates_manifest_on_success(self):
        from homunculus.evolution.merge import MergeManager, MergeResult

        loras = [
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path=str(self.temp_path / "lora1"),
                dataset_snapshot="snap1",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-01",
                candidate_id="lora1",
            )
        ]

        mgr = MergeManager(self.config, self.store)
        mgr._merge_with_mergekit = MagicMock(
            return_value=MergeResult(success=True, output_path="/out")
        )
        mgr._merge_with_mlx = MagicMock(
            return_value=MergeResult(success=True, output_path="/out")
        )

        result = mgr.merge(loras)

        self.assertTrue(result.success)
        self.store.update_merge.assert_called_once()
        updated_manifest = self.store.update_merge.call_args[0][0]
        self.assertEqual(updated_manifest.status, "complete")
        self.assertIsNotNone(updated_manifest.completed_at)

    def test_merge_updates_manifest_on_failure(self):
        from homunculus.evolution.merge import MergeManager, MergeResult

        loras = [
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path=str(self.temp_path / "lora1"),
                dataset_snapshot="snap1",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-01",
                candidate_id="lora1",
            )
        ]

        mgr = MergeManager(self.config, self.store)
        mgr._merge_with_mergekit = MagicMock(
            return_value=MergeResult(success=False, error_message="Something went wrong")
        )
        mgr._merge_with_mlx = MagicMock(
            return_value=MergeResult(success=False, error_message="Something went wrong")
        )

        result = mgr.merge(loras)

        self.assertFalse(result.success)
        self.store.update_merge.assert_called_once()
        updated_manifest = self.store.update_merge.call_args[0][0]
        self.assertEqual(updated_manifest.status, "failed")
        self.assertEqual(updated_manifest.error_message, "Something went wrong")

    def test_generate_mergekit_config_linear(self):
        from homunculus.evolution.merge import MergeManager

        loras = [
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path="/path/lora1",
                dataset_snapshot="snap1",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-01",
                candidate_id="lora1",
            ),
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path="/path/lora2",
                dataset_snapshot="snap2",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-02",
                candidate_id="lora2",
            ),
        ]

        mgr = MergeManager(self.config, self.store)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=["lora1", "lora2"],
            target_base="qwen2.5-coder-1.5b",
            merge_method="linear",
        )

        config = mgr._generate_mergekit_config(manifest, loras)

        self.assertEqual(config["merge_method"], "linear")
        self.assertEqual(config["dtype"], "bfloat16")
        self.assertEqual(len(config["models"]), 3)  # base + 2 loras

    def test_generate_mergekit_config_ties(self):
        from homunculus.evolution.merge import MergeManager

        loras = [
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path="/path/lora1",
                dataset_snapshot="snap1",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-01",
                candidate_id="lora1",
            ),
        ]

        mgr = MergeManager(self.config, self.store)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=["lora1"],
            target_base="qwen2.5-coder-1.5b",
            merge_method="ties",
        )

        config = mgr._generate_mergekit_config(manifest, loras)

        self.assertEqual(config["merge_method"], "ties")
        self.assertEqual(config["base_model"], "qwen2.5-coder-1.5b")
        self.assertTrue(config["parameters"]["normalize"])

    def test_generate_mergekit_config_dare_ties(self):
        from homunculus.evolution.merge import MergeManager

        loras = [
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path="/path/lora1",
                dataset_snapshot="snap1",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-01",
                candidate_id="lora1",
            ),
        ]

        mgr = MergeManager(self.config, self.store)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=["lora1"],
            target_base="qwen2.5-coder-1.5b",
            merge_method="dare_ties",
        )

        config = mgr._generate_mergekit_config(manifest, loras)

        self.assertEqual(config["merge_method"], "dare_ties")
        self.assertEqual(config["base_model"], "qwen2.5-coder-1.5b")

    def test_generate_mergekit_config_unknown_method_raises(self):
        from homunculus.evolution.merge import MergeManager

        loras = [
            AdapterManifest(
                model_id="model",
                base_model="qwen2.5-coder-1.5b",
                adapter_path="/path/lora1",
                dataset_snapshot="snap1",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="promoted",
                created_at="2024-01-01",
                candidate_id="lora1",
            ),
        ]

        mgr = MergeManager(self.config, self.store)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=["lora1"],
            target_base="qwen2.5-coder-1.5b",
            merge_method="unknown_method",
        )

        with self.assertRaises(ValueError) as context:
            mgr._generate_mergekit_config(manifest, loras)

        self.assertIn("Unknown merge method", str(context.exception))

    def test_select_backend_uses_config(self):
        from homunculus.evolution.merge import MergeManager

        # Test explicit mergekit
        self.config.evolution.merge_backend = "mergekit"
        mgr = MergeManager(self.config, self.store)
        self.assertEqual(mgr.backend, "mergekit")

        # Test explicit mlx
        self.config.evolution.merge_backend = "mlx"
        mgr = MergeManager(self.config, self.store)
        self.assertEqual(mgr.backend, "mlx")


class LineageTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Create mock config and store
        self.config = MagicMock()
        self.store = MagicMock()

        # Track appended records
        self.lineage_records: list = []

        def mock_append(record):
            self.lineage_records.append(record)

        self.store.append_lineage = mock_append
        self.store.load_lineage = lambda: list(self.lineage_records)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_register_base_model(self):
        from homunculus.evolution.lineage import LineageTracker

        tracker = LineageTracker(self.config, self.store)
        record = tracker.register_base_model("qwen2.5-coder-1.5b")

        self.assertEqual(record.record_type, "base")
        self.assertEqual(record.generation, 0)
        self.assertEqual(record.parent_ids, [])

    def test_register_lora_links_to_base(self):
        from homunculus.evolution.lineage import LineageTracker

        tracker = LineageTracker(self.config, self.store)

        # Register base first
        tracker.register_base_model("qwen2.5-coder-1.5b")

        # Register LoRA
        candidate = AdapterManifest(
            model_id="qwen2.5-coder-1.5b",
            base_model="qwen2.5-coder-1.5b",
            adapter_path="/path/to/lora",
            dataset_snapshot="snap1",
            snapshot_path=None,
            trainer="mlx-lm",
            metrics={},
            status="trained",
            created_at="2024-01-01",
            candidate_id="lora-001",
        )

        record = tracker.register_lora(candidate, episode_ids=["ep1", "ep2"])

        self.assertEqual(record.record_type, "lora")
        self.assertEqual(record.generation, 0)  # Same as base
        self.assertIn("base-qwen2.5-coder-1.5b", record.parent_ids)
        self.assertEqual(record.episode_ids, ["ep1", "ep2"])

    def test_register_merge_increments_generation(self):
        from homunculus.evolution.lineage import LineageTracker

        tracker = LineageTracker(self.config, self.store)

        # Setup: base -> lora1, lora2
        tracker.register_base_model("qwen2.5-coder-1.5b")

        for i, cid in enumerate(["lora-001", "lora-002"]):
            candidate = AdapterManifest(
                model_id="qwen2.5-coder-1.5b",
                base_model="qwen2.5-coder-1.5b",
                adapter_path=f"/path/to/{cid}",
                dataset_snapshot=f"snap{i}",
                snapshot_path=None,
                trainer="mlx-lm",
                metrics={},
                status="trained",
                created_at="2024-01-01",
                candidate_id=cid,
            )
            tracker.register_lora(candidate, episode_ids=[f"ep{i}"])

        # Merge
        merge = MergeManifest(
            merge_id="merge-001",
            source_loras=["lora-001", "lora-002"],
            target_base="qwen2.5-coder-1.5b",
            merge_method="linear",
            output_path="/path/to/merged",
        )

        record = tracker.register_merge(merge, "qwen2.5-coder-1.5b-gen1")

        self.assertEqual(record.record_type, "merged")
        self.assertEqual(record.generation, 1)  # Incremented
        self.assertIn("ep0", record.episode_ids)
        self.assertIn("ep1", record.episode_ids)

    def test_get_ancestors(self):
        from homunculus.evolution.lineage import LineageTracker

        # Pre-populate lineage
        self.lineage_records = [
            LineageRecord(record_id="base", record_type="base", model_id="m", generation=0),
            LineageRecord(record_id="lora1", record_type="lora", model_id="m", parent_ids=["base"], generation=0),
            LineageRecord(record_id="merge1", record_type="merged", model_id="m", parent_ids=["base", "lora1"], generation=1),
        ]

        tracker = LineageTracker(self.config, self.store)
        ancestors = tracker.get_ancestors("merge1")

        ancestor_ids = [a.record_id for a in ancestors]
        self.assertIn("base", ancestor_ids)
        self.assertIn("lora1", ancestor_ids)

    def test_export_graph(self):
        from homunculus.evolution.lineage import LineageTracker

        self.lineage_records = [
            LineageRecord(record_id="base", record_type="base", model_id="m", generation=0),
            LineageRecord(record_id="lora1", record_type="lora", model_id="m", parent_ids=["base"], generation=0),
        ]

        tracker = LineageTracker(self.config, self.store)
        graph = tracker.export_graph()

        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["generations"], 1)  # Only gen 0

    def test_get_descendants(self):
        from homunculus.evolution.lineage import LineageTracker

        # Pre-populate lineage: base -> lora1 -> merge1
        self.lineage_records = [
            LineageRecord(record_id="base", record_type="base", model_id="m", generation=0),
            LineageRecord(record_id="lora1", record_type="lora", model_id="m", parent_ids=["base"], generation=0),
            LineageRecord(record_id="merge1", record_type="merged", model_id="m", parent_ids=["lora1"], generation=1),
        ]

        tracker = LineageTracker(self.config, self.store)
        descendants = tracker.get_descendants("base")

        descendant_ids = [d.record_id for d in descendants]
        self.assertIn("lora1", descendant_ids)
        self.assertIn("merge1", descendant_ids)

    def test_get_episodes_for_model(self):
        from homunculus.evolution.lineage import LineageTracker

        # Pre-populate: base -> lora1, lora2 -> merge
        self.lineage_records = [
            LineageRecord(record_id="base", record_type="base", model_id="m", generation=0),
            LineageRecord(record_id="lora1", record_type="lora", model_id="m", parent_ids=["base"], episode_ids=["ep1", "ep2"], generation=0),
            LineageRecord(record_id="lora2", record_type="lora", model_id="m", parent_ids=["base"], episode_ids=["ep3"], generation=0),
            LineageRecord(record_id="merge1", record_type="merged", model_id="m", parent_ids=["lora1", "lora2"], episode_ids=["ep1", "ep2", "ep3"], generation=1),
        ]

        tracker = LineageTracker(self.config, self.store)
        episodes = tracker.get_episodes_for_model("merge1")

        self.assertIn("ep1", episodes)
        self.assertIn("ep2", episodes)
        self.assertIn("ep3", episodes)

    def test_ensure_base_registered_idempotent(self):
        from homunculus.evolution.lineage import LineageTracker

        tracker = LineageTracker(self.config, self.store)

        # Register twice
        record1 = tracker.ensure_base_registered("qwen2.5-coder-1.5b")
        record2 = tracker.ensure_base_registered("qwen2.5-coder-1.5b")

        # Should only have one record
        self.assertEqual(len(self.lineage_records), 1)
        self.assertEqual(record1.record_id, record2.record_id)

    def test_get_current_generation_empty(self):
        from homunculus.evolution.lineage import LineageTracker

        tracker = LineageTracker(self.config, self.store)
        gen = tracker.get_current_generation()

        self.assertEqual(gen, 0)

    def test_get_current_generation_with_records(self):
        from homunculus.evolution.lineage import LineageTracker

        self.lineage_records = [
            LineageRecord(record_id="base", record_type="base", model_id="m", generation=0),
            LineageRecord(record_id="merge1", record_type="merged", model_id="m", generation=1),
            LineageRecord(record_id="merge2", record_type="merged", model_id="m", generation=2),
        ]

        tracker = LineageTracker(self.config, self.store)
        gen = tracker.get_current_generation()

        self.assertEqual(gen, 2)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.config = MagicMock()
        self.config.canary_commands = None  # Use None instead of [] to match getattr behavior
        self.config.evolution.validation_timeout_seconds = 30
        self.config.evolution.coherence_prompt = "Test prompt"
        self.config.evolution.coherence_min_tokens = 10

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_validation_fails_no_path(self):
        from homunculus.evolution.validation import MergeValidator

        validator = MergeValidator(self.config)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=[],
            target_base="model",
            merge_method="linear",
            output_path=None,
        )

        result = validator._validate_load(manifest)
        self.assertFalse(result.passed)
        self.assertEqual(result.stage, "load")

    def test_load_validation_fails_missing_dir(self):
        from homunculus.evolution.validation import MergeValidator

        validator = MergeValidator(self.config)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=[],
            target_base="model",
            merge_method="linear",
            output_path="/nonexistent/path",
        )

        result = validator._validate_load(manifest)
        self.assertFalse(result.passed)

    def test_load_validation_passes_with_files(self):
        from homunculus.evolution.validation import MergeValidator

        # Create mock model directory
        model_dir = self.temp_path / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_text("fake")

        validator = MergeValidator(self.config)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=[],
            target_base="model",
            merge_method="linear",
            output_path=str(model_dir),
        )

        result = validator._validate_load(manifest)
        self.assertTrue(result.passed)

    def test_canary_validation_skips_when_none(self):
        from homunculus.evolution.validation import MergeValidator

        validator = MergeValidator(self.config)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=[],
            target_base="model",
            merge_method="linear",
            output_path=str(self.temp_path),
        )

        result = validator._validate_canary(manifest)
        self.assertTrue(result.passed)
        self.assertIn("skipped", result.message.lower())

    def test_is_repetitive_detects_loops(self):
        from homunculus.evolution.validation import MergeValidator

        validator = MergeValidator(self.config)

        # Non-repetitive
        self.assertFalse(validator._is_repetitive(
            "The quick brown fox jumps over the lazy dog."
        ))

        # Repetitive
        self.assertTrue(validator._is_repetitive(
            "the end the end the end the end the end the end the end"
        ))

    def test_load_validation_fails_missing_config(self):
        from homunculus.evolution.validation import MergeValidator

        # Create mock model directory without config.json
        model_dir = self.temp_path / "model_no_config"
        model_dir.mkdir()
        (model_dir / "model.safetensors").write_text("fake")

        validator = MergeValidator(self.config)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=[],
            target_base="model",
            merge_method="linear",
            output_path=str(model_dir),
        )

        result = validator._validate_load(manifest)
        self.assertFalse(result.passed)
        self.assertIn("config.json", result.message)

    def test_load_validation_fails_no_weights(self):
        from homunculus.evolution.validation import MergeValidator

        # Create mock model directory without weight files
        model_dir = self.temp_path / "model_no_weights"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")

        validator = MergeValidator(self.config)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=[],
            target_base="model",
            merge_method="linear",
            output_path=str(model_dir),
        )

        result = validator._validate_load(manifest)
        self.assertFalse(result.passed)
        self.assertIn("weight", result.message.lower())


class TrainingManagerEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        self.config = MagicMock()
        self.config.evolution.enabled = False
        self.config.evolution.max_merge_attempts = 3
        self.config.paths.runtime_dir = self.temp_path / "runtime"
        self.config.paths.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.store = MagicMock()
        self.builder = MagicMock()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_should_merge_respects_config(self):
        from homunculus.trainer.manager import TrainingManager

        mgr = TrainingManager(self.config, self.store, self.builder)
        self.assertFalse(mgr.should_merge())

    def test_consecutive_failure_tracking(self):
        from homunculus.trainer.manager import TrainingManager

        self.config.evolution.enabled = True
        mgr = TrainingManager(self.config, self.store, self.builder)

        # Simulate failures using persistent storage
        mgr._set_consecutive_merge_failures(2)
        self.assertFalse(mgr.should_generate_merge_failure_task())

        mgr._set_consecutive_merge_failures(3)
        self.assertTrue(mgr.should_generate_merge_failure_task())

        mgr.reset_merge_failure_count()
        self.assertEqual(mgr._get_consecutive_merge_failures(), 0)

    def test_consecutive_failure_persists_to_file(self):
        from homunculus.trainer.manager import TrainingManager

        self.config.evolution.enabled = True
        mgr = TrainingManager(self.config, self.store, self.builder)

        # Set failures
        mgr._set_consecutive_merge_failures(5)

        # Create new manager instance to verify persistence
        mgr2 = TrainingManager(self.config, self.store, self.builder)
        self.assertEqual(mgr2._get_consecutive_merge_failures(), 5)


class IntegrationTests(unittest.TestCase):
    def test_full_validation_pipeline(self):
        from homunculus.evolution.validation import MergeValidator, FullValidationResult

        temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(temp_dir.name)

        # Create mock model
        model_dir = temp_path / "merged"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_text("fake")

        config = MagicMock()
        config.canary_commands = None
        config.evolution.validation_timeout_seconds = 30
        config.evolution.coherence_prompt = "Test"
        config.evolution.coherence_min_tokens = 5

        validator = MergeValidator(config)
        manifest = MergeManifest(
            merge_id="test",
            source_loras=["lora1"],
            target_base="model",
            merge_method="linear",
            output_path=str(model_dir),
        )

        # Full validation will fail at coherence (no real model)
        # but load and canary should pass
        result = validator.validate(manifest)

        # Verify structure
        self.assertIsInstance(result, FullValidationResult)
        self.assertGreaterEqual(len(result.stages), 1)
        self.assertEqual(result.stages[0].stage, "load")

        # With the coherence fix, behavior depends on whether a backend is installed.
        # On bare CI / Windows-no-backend: coherence fails closed -> pipeline fails.
        # On a real ML host with MLX or transformers: coherence still fails because the
        # fake "model.safetensors = 'fake'" cannot be loaded as a real model.
        # Either way, never a silent pass on garbage.
        if _has_inference_backend():
            # Backend present -> load attempt against fake weights raises an error
            # -> result.passed should be False with a real failure reason.
            self.assertFalse(result.passed)
        else:
            self.assertFalse(result.passed)
            self.assertTrue(
                any("backend_unavailable" in stage.message.lower()
                    for stage in result.stages),
                f"expected backend_unavailable in stages, got: {[s.message for s in result.stages]}",
            )

        temp_dir.cleanup()

    def test_validation_result_to_dict(self):
        from homunculus.evolution.validation import ValidationResult, FullValidationResult

        stage = ValidationResult(
            stage="load",
            passed=True,
            message="All good",
            details={"files": ["a.bin"]},
        )

        full = FullValidationResult(passed=True, stages=[stage])
        result_dict = full.to_dict()

        self.assertTrue(result_dict["passed"])
        self.assertEqual(len(result_dict["stages"]), 1)
        self.assertEqual(result_dict["stages"][0]["stage"], "load")
        self.assertEqual(result_dict["stages"][0]["message"], "All good")


class TaskGeneratorMergeTests(unittest.TestCase):
    def test_generate_merge_failure_task(self):
        from homunculus.task_generator.generator import TaskGenerator

        generator = TaskGenerator(store=None)
        task = generator.generate_merge_failure_task(
            failure_count=3,
            last_error="mergekit not found",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.source, "introspection")
        self.assertEqual(task.introspection_mode, "merge_failure")
        self.assertEqual(task.priority, 0.9)
        self.assertIn("merge", task.task_id.lower())
        self.assertIn("3", task.prompt)
        self.assertIn("mergekit not found", task.prompt)

    def test_generate_merge_failure_task_no_error(self):
        from homunculus.task_generator.generator import TaskGenerator

        generator = TaskGenerator(store=None)
        task = generator.generate_merge_failure_task(
            failure_count=5,
            last_error=None,
        )

        self.assertIsNotNone(task)
        self.assertIn("Unknown", task.prompt)
        self.assertIn("5", task.prompt)


class CoherenceFailClosedTests(unittest.TestCase):
    """Coherence stage must fail closed when no inference backend is available.

    Regression test for the silent-failure bug where _validate_coherence
    returned passed=True on backend-less machines (Windows w/o transformers,
    Linux w/o MLX or transformers).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "merged"
        self.output.mkdir()
        (self.output / "config.json").write_text("{}", encoding="utf-8")
        (self.output / "model.safetensors").write_text("fake", encoding="utf-8")

        # Mirror the pattern used by ValidationTests: a MagicMock config is
        # sufficient because _validate_coherence only reads coherence_prompt
        # and coherence_min_tokens.
        self.config = MagicMock()
        self.config.canary_commands = None
        self.config.evolution.validation_timeout_seconds = 30
        self.config.evolution.coherence_prompt = "Test prompt"
        self.config.evolution.coherence_min_tokens = 10

    def tearDown(self):
        self.tmp.cleanup()

    def _make_validator(self):
        from homunculus.evolution.validation import MergeValidator
        return MergeValidator(self.config)

    def test_coherence_fails_closed_when_no_backend(self):
        manifest = MergeManifest(
            merge_id="m1",
            source_loras=[],
            target_base="b",
            merge_method="linear",
            output_path=str(self.output),
        )
        validator = self._make_validator()

        # Force both backends to be unavailable by intercepting imports.
        # __import__ is always a function on the builtins module; reach it
        # via the builtins module to avoid the dict-vs-module __builtins__
        # ambiguity that varies by execution context.
        import builtins
        original_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if (
                name in ("mlx_lm", "transformers")
                or name.startswith("mlx_lm.")
                or name.startswith("transformers.")
            ):
                raise ImportError(f"forced absence of {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=blocking_import):
            result = validator._validate_coherence(manifest)

        self.assertFalse(
            result.passed,
            "coherence MUST fail closed when no backend is available",
        )
        self.assertEqual(result.stage, "coherence")
        self.assertIn("backend_unavailable", result.message.lower())


class CoherenceTokenSlicingTests(unittest.TestCase):
    """Coherence stage must NOT count prompt tokens as output tokens.

    Regression tests for three orthogonal hardening fixes in
    _generate_transformers and _is_repetitive:
      1. Prompt-token leak: returning the prompt unchanged (zero new tokens)
         must NOT pass min_tokens=50 just because the prompt is ~10 words.
      2. _is_repetitive precision: short outputs (<10 words) of pure
         repetition must be flagged, not silently passed.
    """

    def setUp(self):
        # Mirror CoherenceFailClosedTests: a MagicMock config is sufficient
        # because _validate_coherence only reads the two coherence_* keys.
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "merged"
        self.output.mkdir()
        (self.output / "config.json").write_text("{}", encoding="utf-8")
        (self.output / "model.safetensors").write_text("fake", encoding="utf-8")

        self.config = MagicMock()
        self.config.canary_commands = None
        self.config.evolution.validation_timeout_seconds = 30
        self.config.evolution.coherence_prompt = (
            "Write a Python function that returns the nth Fibonacci number."
        )
        self.config.evolution.coherence_min_tokens = 50

    def tearDown(self):
        self.tmp.cleanup()

    def _make_validator(self):
        from homunculus.evolution.validation import MergeValidator
        return MergeValidator(self.config)

    def test_token_count_excludes_prompt(self):
        # Post-fix contract: when the model generates zero new tokens,
        # _generate_transformers returns "" (empty generated suffix) and
        # the empty-output check fires. We simulate the post-fix correct
        # behavior by returning "" from the patched method, and assert
        # that _validate_coherence catches it. This guards the integration
        # path: the empty-output rejection plus the source-level slice
        # check (test_generate_transformers_strips_prompt) together prove
        # zero-new-token generation cannot pass coherence.
        self.assertTrue(self.config.evolution.coherence_prompt)

        validator = self._make_validator()
        manifest = MergeManifest(
            merge_id="m",
            source_loras=[],
            target_base="b",
            merge_method="linear",
            output_path=str(self.output),
        )

        with patch("platform.system", return_value="Linux"), \
             patch.object(
                 validator,
                 "_generate_transformers",
                 return_value="",  # zero new tokens generated
             ):
            result = validator._validate_coherence(manifest)

        self.assertFalse(
            result.passed,
            "Zero-new-token generation (empty output after prompt slice) "
            "MUST NOT pass coherence",
        )
        self.assertIn("empty", result.message.lower())

    def test_generate_transformers_strips_prompt(self):
        """The transformers generator MUST slice off prompt tokens.

        We assert the source code pattern directly because importing
        torch/transformers in CI is not guaranteed. The contract is:
        the method must NOT decode the full output_ids tensor — it must
        slice [inputs.input_ids.shape[1]:] before decoding.
        """
        from pathlib import Path
        src_path = (
            Path(__file__).resolve().parent.parent
            / "homunculus"
            / "evolution"
            / "validation.py"
        )
        src = src_path.read_text(encoding="utf-8")
        # Find the _generate_transformers method body
        idx = src.find("def _generate_transformers")
        self.assertGreater(idx, -1, "_generate_transformers method must exist")
        # Slice a generous window around the method
        method_src = src[idx:idx + 2000]
        # Pre-fix smell: decoding outputs[0] directly without slicing
        self.assertNotIn(
            "tokenizer.decode(outputs[0]",
            method_src,
            "BUG: decoding outputs[0] includes the prompt; must slice "
            "[inputs.input_ids.shape[1]:] first",
        )
        # Post-fix marker: must slice off prompt tokens before decode
        self.assertIn(
            "inputs.input_ids.shape[1]",
            method_src,
            "FIX: must slice generated tokens with [inputs.input_ids.shape[1]:]",
        )
        # Determinism: greedy decoding (no sampling)
        self.assertIn(
            "do_sample=False",
            method_src,
            "FIX: coherence generation must be deterministic (do_sample=False)",
        )
        # Memory hygiene: del + cuda cache clear in finally
        self.assertIn("del model", method_src,
                      "FIX: must del model to release VRAM")
        self.assertIn(
            "torch.cuda.empty_cache",
            method_src,
            "FIX: must call torch.cuda.empty_cache() to release VRAM",
        )

    def test_short_repetitive_output_detected(self):
        """Regression for _is_repetitive: 9 identical words should be flagged."""
        validator = self._make_validator()
        # 9 repeated words — under the old <10 word early-return, this slipped
        # through; under the new 4-gram dominance check it must be caught.
        self.assertTrue(
            validator._is_repetitive("the the the the the the the the the"),
            "9-word pure repetition must be flagged as degenerate",
        )


class EvolutionStateResilienceTests(unittest.TestCase):
    """Verify trainer evolution_state.json parsing is crash-safe.

    Regression for: corrupt JSON, non-int values, negative values must
    all default to 0 instead of propagating exceptions or wrong types.
    Writes must be atomic so an interrupted write can't produce a partial file.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp_dir.name) / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_mgr(self):
        from homunculus.trainer.manager import TrainingManager

        config = MagicMock()
        config.evolution.enabled = True
        config.evolution.max_merge_attempts = 3
        config.paths.runtime_dir = self.runtime
        return TrainingManager(config, store=MagicMock(), builder=MagicMock())

    def test_corrupt_json_returns_zero(self):
        (self.runtime / "evolution_state.json").write_text(
            "not json{", encoding="utf-8"
        )
        self.assertEqual(self._make_mgr()._get_consecutive_merge_failures(), 0)

    def test_non_int_value_returns_zero(self):
        (self.runtime / "evolution_state.json").write_text(
            '{"consecutive_merge_failures": "abc"}', encoding="utf-8"
        )
        self.assertEqual(self._make_mgr()._get_consecutive_merge_failures(), 0)

    def test_negative_value_returns_zero(self):
        (self.runtime / "evolution_state.json").write_text(
            '{"consecutive_merge_failures": -5}', encoding="utf-8"
        )
        self.assertEqual(self._make_mgr()._get_consecutive_merge_failures(), 0)

    def test_set_is_atomic_no_temp_files_left(self):
        mgr = self._make_mgr()
        mgr._set_consecutive_merge_failures(7)
        self.assertEqual(mgr._get_consecutive_merge_failures(), 7)
        # Verify temp file does not linger
        leftovers = [
            p for p in self.runtime.glob("evolution_state.json*")
            if p.name != "evolution_state.json"
        ]
        self.assertEqual(
            leftovers, [],
            f"unexpected temp file leftovers: {leftovers}",
        )


if __name__ == "__main__":
    unittest.main()
