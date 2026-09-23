from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def migrate(connection: sqlite3.Connection, directory: Path) -> list[str]:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row["version"]
        for row in connection.execute("SELECT version FROM schema_migrations")
    }
    new_versions: list[str] = []
    for migration in sorted(directory.glob("*.sql")):
        version = migration.stem
        if version in applied:
            continue
        with connection:
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, utc_now()),
            )
        new_versions.append(version)
    return new_versions

