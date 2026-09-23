from __future__ import annotations

import sqlite3
import subprocess
import sys
import unittest

import tests  # noqa: F401 - adds the src layout to sys.path for direct unittest runs

from tests.helpers import ROOT


class CliTests(unittest.TestCase):
    def test_select_defaults_to_dry_run(self):
        database = ROOT / "data" / "world.db"
        before = 0
        if database.exists():
            with sqlite3.connect(database) as connection:
                before = connection.execute("SELECT COUNT(*) FROM selections").fetchone()[0]
        completed = subprocess.run(
            [sys.executable, "main.py", "select", "--date", "2026-09-21", "--seed", "test"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("dry-run", completed.stdout)
        with sqlite3.connect(database) as connection:
            after = connection.execute("SELECT COUNT(*) FROM selections").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
