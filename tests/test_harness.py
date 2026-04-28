from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from homunculus.harness import run_harness_check


class HarnessCheckTests(unittest.TestCase):
    def test_current_repo_harness_check_passes(self) -> None:
        root = Path(__file__).resolve().parent.parent
        report = run_harness_check(root, strict=True)
        details = {check.name: check.detail for check in report.checks}
        self.assertTrue(report.ok, details)

    def test_cli_json_output_passes(self) -> None:
        root = Path(__file__).resolve().parent.parent
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "homunculus.cli",
                "harness-check",
                "--strict",
                "--json",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])

    def test_stale_guidance_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_minimal_harness(root)
            (root / "README.md").write_text(
                "promotion is intentionally manual\n",
                encoding="utf-8",
            )

            report = run_harness_check(root, strict=True)

            self.assertFalse(report.ok)
            stale = next(check for check in report.checks if check.name == "stale-guidance")
            self.assertFalse(stale.passed)
            self.assertIn("README.md", stale.detail)

    def _write_minimal_harness(self, root: Path) -> None:
        for directory in [
            root / "docs",
            root / "homunculus",
            root / ".github" / "workflows",
        ]:
            directory.mkdir(parents=True, exist_ok=True)

        (root / "AGENTS.md").write_text(
            "\n".join(
                [
                    "docs/index.md",
                    "docs/harness-engineering.md",
                    "docs/architecture.md",
                    "python -m homunculus.cli harness-check --strict",
                ]
            ),
            encoding="utf-8",
        )
        (root / "CLAUDE.md").write_text("Follow AGENTS.md\n", encoding="utf-8")
        (root / "README.md").write_text("ok\n", encoding="utf-8")
        (root / "docs" / "index.md").write_text(
            "\n".join(
                [
                    "harness-engineering.md",
                    "architecture.md",
                    "operator-guide.md",
                    "setup-and-configuration.md",
                    "quality-score.md",
                ]
            ),
            encoding="utf-8",
        )
        for name in [
            "harness-engineering.md",
            "architecture.md",
            "operator-guide.md",
            "setup-and-configuration.md",
            "quality-score.md",
        ]:
            (root / "docs" / name).write_text("ok\n", encoding="utf-8")
        (root / "homunculus.example.toml").write_text(
            """
[daemon]
target_workspace = "self"
auto_commit_on_accept = true

[evolution]
auto_promote = true
auto_apply = true
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (root / ".github" / "workflows" / "harness.yml").write_text(
            """
steps:
  - run: python -m homunculus.cli harness-check --strict
  - run: python -m unittest discover -q
""".strip()
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
