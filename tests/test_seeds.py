from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401 - adds the src layout to sys.path for direct unittest runs

from worldnote.db import connect, migrate
from worldnote.seeds import SeedError, import_seeds


ROOT = Path(__file__).resolve().parents[1]


class SeedTests(unittest.TestCase):
    def test_seed_import_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "test.db")
            migrate(connection, ROOT / "data" / "migrations")
            seed_file = ROOT / "data" / "seeds" / "locations.json"
            import_seeds(connection, seed_file)
            import_seeds(connection, seed_file)
            count = connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
            self.assertEqual(count, 12)
            connection.close()

    def test_alias_collision_stops_import(self):
        payload = json.loads(
            (ROOT / "data" / "seeds" / "locations.json").read_text(encoding="utf-8")
        )
        first = copy.deepcopy(payload["locations"][0])
        second = copy.deepcopy(payload["locations"][1])
        first["aliases"] = ["shared alias"]
        second["aliases"] = ["Shared-Alias"]
        payload["locations"] = [first, second]
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            seed_path = directory_path / "seeds.json"
            seed_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            connection = connect(directory_path / "test.db")
            migrate(connection, ROOT / "data" / "migrations")
            with self.assertRaisesRegex(SeedError, "别名冲突"):
                import_seeds(connection, seed_path)
            connection.close()


if __name__ == "__main__":
    unittest.main()
