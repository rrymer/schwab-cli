"""Credential-free packaging tests using Python's standard library only."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "pyproject.toml").open("rb") as stream:
            cls.metadata = tomllib.load(stream)

    def test_console_script_declared(self):
        self.assertEqual(
            self.metadata["project"]["scripts"]["schwab"],
            "schwab_cli.cli:main",
        )

    def test_pep_621_dependencies_are_a_list(self):
        dependencies = self.metadata["project"]["dependencies"]
        self.assertIsInstance(dependencies, list)

    def test_src_package_configuration(self):
        self.assertIn(
            "src/schwab_cli",
            self.metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"],
        )
        self.assertEqual(self.metadata["build-system"]["build-backend"], "hatchling.build")

    def test_module_entry_point_help_is_credential_free(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        completed = subprocess.run(
            [sys.executable, "-m", "schwab_cli.cli", "--help"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("positions", completed.stdout)
        self.assertIn("auth", completed.stdout)

    def test_console_main_callable_without_credentials(self):
        from schwab_cli.cli import main
        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
