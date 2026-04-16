"""Verify that all installable subpackages are declared in pyproject.toml."""
import importlib
import unittest
from pathlib import Path

import tomllib


REQUIRED_SUBPACKAGES = [
    "homunculus",
    "homunculus.orchestrator",
    "homunculus.memory_client",
    "homunculus.task_runner",
    "homunculus.dataset_builder",
    "homunculus.trainer",
    "homunculus.introspection",
    "homunculus.task_generator",
    "homunculus.evolution",
]


class PackagingTests(unittest.TestCase):
    def test_pyproject_declares_all_subpackages(self):
        root = Path(__file__).resolve().parent.parent
        with (root / "pyproject.toml").open("rb") as fh:
            cfg = tomllib.load(fh)
        tool_setuptools = cfg.get("tool", {}).get("setuptools", {})
        # Support both explicit packages list and find: directive
        if "packages" in tool_setuptools and isinstance(tool_setuptools["packages"], list):
            declared = set(tool_setuptools["packages"])
            missing = set(REQUIRED_SUBPACKAGES) - declared
            self.assertFalse(
                missing,
                f"pyproject.toml is missing packages: {sorted(missing)}",
            )
        else:
            # Using find: directive — verify it's configured
            find_cfg = tool_setuptools.get("packages", {}).get("find", {})
            include = find_cfg.get("include", [])
            self.assertTrue(
                any("homunculus" in pat for pat in include),
                "find: directive must include homunculus* pattern",
            )

    def test_every_required_subpackage_imports(self):
        for name in REQUIRED_SUBPACKAGES:
            with self.subTest(package=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
