"""Tests for homunculus.policy.GuardrailEngine compile-at-load behavior."""
from __future__ import annotations

import re as _re
import tempfile
import unittest
from pathlib import Path


class GuardrailCompileAtLoadTests(unittest.TestCase):
    """Invalid regex must surface at load_config, not mid-episode.

    Rationale: a soak that runs into an invalid guardrail crashes
    episode N+1 with a re.error traceback at a moment the operator
    can't reach the console. Fail loud at launch instead.
    """

    def _write_config(self, root: str, guardrails_block: str) -> Path:
        """Write a temp homunculus.toml.

        Starts from homunculus.example.toml, strips the existing
        ``[guardrails]`` section (to avoid duplicate-table TOML errors),
        re-points ``[workspaces.self].path`` at ``root`` so preflight
        doesn't demand a clean repo, and appends the caller's guardrails
        block.
        """
        source = Path("homunculus.example.toml").read_text(encoding="utf-8")
        # Strip the existing [guardrails] ... [workspaces.self] block;
        # keep everything before [guardrails] and from [workspaces.self] on.
        guardrails_start = source.index("[guardrails]")
        workspaces_start = source.index("[workspaces.self]", guardrails_start)
        trimmed = source[:guardrails_start] + source[workspaces_start:]
        # Repoint workspaces.self.path at the temp root
        trimmed = trimmed.replace(
            '[workspaces.self]\npath = "."',
            f'[workspaces.self]\npath = "{Path(root).as_posix()}"',
            1,
        )
        config_path = Path(root) / "config.toml"
        config_path.write_text(trimmed + "\n" + guardrails_block, encoding="utf-8")
        return config_path

    def test_invalid_block_pattern_fails_load_config(self):
        from homunculus.config import load_config
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                '[guardrails]\n'
                '[[guardrails.block_patterns]]\n'
                'pattern = "(unclosed"\n'
                'message = "bad regex"\n',
            )
            with self.assertRaises(_re.error):
                load_config(path)

    def test_valid_block_pattern_is_precompiled(self):
        from homunculus.config import CompiledGuardrailRule, load_config
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                '[guardrails]\n'
                '[[guardrails.block_patterns]]\n'
                'pattern = "rm -rf"\n'
                'message = "destructive"\n',
            )
            settings = load_config(path)
            rules = settings.guardrails.block_patterns
            self.assertEqual(len(rules), 1)
            self.assertIsInstance(rules[0], CompiledGuardrailRule)
            self.assertIsInstance(rules[0].regex, _re.Pattern)
            self.assertEqual(rules[0].pattern, "rm -rf")

    def test_engine_uses_precompiled_regex(self):
        """GuardrailEngine.evaluate must call the pre-compiled
        ``regex.search``, not ``re.search`` on the string pattern.

        Prove it by handing the engine a rule whose ``pattern`` string
        is gibberish and whose ``regex`` matches anything — the engine
        must honor the regex, proving it doesn't recompile.
        """
        from homunculus.config import CompiledGuardrailRule, GuardrailSettings
        from homunculus.policy import GuardrailEngine

        catch_all = CompiledGuardrailRule(
            pattern="(this would not compile",
            message="blocked",
            regex=_re.compile(r".*"),
        )
        settings = GuardrailSettings(block_patterns=[catch_all], warn_patterns=[])
        engine = GuardrailEngine(settings)
        decision = engine.evaluate("hello", candidate_patch=None, memories=[])
        self.assertFalse(decision.allowed)
        self.assertIn("blocked", decision.blocked_reasons)


if __name__ == "__main__":
    unittest.main()
