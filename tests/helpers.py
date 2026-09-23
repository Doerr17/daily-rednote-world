from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from worldnote.db import connect, migrate
from worldnote.seeds import import_seeds


ROOT = Path(__file__).resolve().parents[1]


def seeded_database(temp_dir: Path) -> sqlite3.Connection:
    connection = connect(temp_dir / "test.db")
    migrate(connection, ROOT / "data" / "migrations")
    import_seeds(connection, ROOT / "data" / "seeds" / "locations.json")
    return connection


def write_seed(path: Path, locations: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {"seed_version": 1, "checked_on": "2026-09-21", "locations": locations},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path

