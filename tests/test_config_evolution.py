"""Verify EvolutionSettings reads every [evolution] key documented in example.toml."""
import tempfile
import unittest
from pathlib import Path

from homunculus.config import load_config


# Minimal valid TOML — every section needed by load_config, with the FULL
# [evolution] block as documented in homunculus.example.toml plus the
# implementation-specific keys (max_merge_attempts, etc.) for completeness.
EXAMPLE_TOML = """
[teacher]
provider = "openai-compatible"
model = "x"
base_url = "http://example"
endpoint = "/c"
api_key_env = "X"

[student]
model_id = "x"
generate_command = ["echo"]
train_command = ["echo"]

[memory]
base_url = "http://example"
search_endpoint = "/s"
store_endpoint = "/x"
bearer_token_env = "Y"

[thresholds]
train_after_samples = 1
train_after_days = 1
max_self_generated_ratio = 0.5
min_eval_success_delta = 0.0

[promotion]
allow_zero_canary_regressions = true
min_task_success_delta = 0.0
max_tool_misuse_increase = 0.0

[paths]
root = "."
traces_dir = "t"
datasets_dir = "d"
models_dir = "m"
runtime_dir = "r"
seed_sft_path = "s.jsonl"
seed_dpo_path = "d.jsonl"

[dpo]
enabled = false

[daemon]
enabled = true
cycle_interval_minutes = 1
max_episodes_per_cycle = 1

[evolution]
enabled = true
auto_promote = true
auto_apply = false
auto_train_after_samples = 50
auto_merge_after_loras = 7
rollback_on_degradation = true
max_merge_attempts = 4
validation_timeout_seconds = 120

[guardrails]

[workspaces.self]
path = "."
"""


class EvolutionConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".toml", delete=False, encoding="utf-8"
        )
        self.tmp.write(EXAMPLE_TOML)
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_loads_all_documented_evolution_keys(self):
        cfg = load_config(self.path)
        self.assertTrue(cfg.evolution.enabled)
        self.assertTrue(cfg.evolution.auto_promote)
        self.assertFalse(cfg.evolution.auto_apply)
        self.assertEqual(cfg.evolution.auto_train_after_samples, 50)
        self.assertEqual(cfg.evolution.auto_merge_after_loras, 7)
        self.assertTrue(cfg.evolution.rollback_on_degradation)
        self.assertEqual(cfg.evolution.max_merge_attempts, 4)


class EvolutionConfigBackcompatTests(unittest.TestCase):
    """The old key name `merge_after_loras` should still work as an alias."""
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".toml", delete=False, encoding="utf-8"
        )
        # Same as above but with old key name
        self.tmp.write(EXAMPLE_TOML.replace(
            "auto_merge_after_loras = 7",
            "merge_after_loras = 9",
        ))
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_old_key_name_still_works(self):
        cfg = load_config(self.path)
        self.assertEqual(cfg.evolution.auto_merge_after_loras, 9)


if __name__ == "__main__":
    unittest.main()
