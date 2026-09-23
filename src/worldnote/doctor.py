from __future__ import annotations

import sqlite3
import sys
import hashlib
import importlib.util
import json
from pathlib import Path

from .config import ProjectConfig
from .research_validation import validate_schema_documents


def run_doctor(connection: sqlite3.Connection, config: ProjectConfig) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    checks.append(
        (
            "Python",
            sys.version_info >= (3, 10),
            ".".join(str(value) for value in sys.version_info[:3]),
        )
    )
    migrations = connection.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    checks.append(("Database migrations", migrations > 0, f"{migrations} applied"))
    locations = connection.execute("SELECT COUNT(*) AS n FROM locations WHERE active=1").fetchone()["n"]
    checks.append(
        (
            "Seed locations",
            locations >= config.minimum_seed_locations,
            f"{locations} active; minimum {config.minimum_seed_locations}",
        )
    )
    conflicts = connection.execute(
        "SELECT COUNT(*) AS n FROM ("
        "SELECT alias_normalized FROM location_aliases GROUP BY alias_normalized "
        "HAVING COUNT(DISTINCT location_id) > 1)"
    ).fetchone()["n"]
    checks.append(("Alias uniqueness", conflicts == 0, f"{conflicts} conflicts"))
    source_missing = connection.execute(
        "SELECT COUNT(*) AS n FROM topic_candidates t "
        "WHERE t.active=1 AND NOT EXISTS "
        "(SELECT 1 FROM candidate_sources s WHERE s.topic_id=t.id)"
    ).fetchone()["n"]
    checks.append(("Candidate sources", source_missing == 0, f"{source_missing} missing"))
    checks.append(("Seed file", config.seed_file.is_file(), str(config.seed_file)))
    try:
        validate_schema_documents(config.root / "schemas")
        checks.append(("Research/content/media schemas", True, "10 JSON schemas valid"))
    except Exception as exc:
        checks.append(("Research/content schemas", False, str(exc)))
    cache = config.root / config.research["cache_directory"]
    try:
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(("Research cache", True, str(cache)))
    except OSError as exc:
        checks.append(("Research cache", False, str(exc)))
    packages = ["jinja2", "PIL", "playwright", "shapefile"]
    missing_packages = [name for name in packages if importlib.util.find_spec(name) is None]
    checks.append(
        (
            "Visual dependencies",
            not missing_packages,
            "available" if not missing_packages else f"missing: {', '.join(missing_packages)}",
        )
    )
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=str(config.render["browser_channel"]), headless=True
            )
            browser.close()
        checks.append(("Render browser", True, str(config.render["browser_channel"])))
    except Exception as exc:
        checks.append(("Render browser", False, str(exc)))
    map_directory = config.root / config.map["directory"]
    archive = map_directory / "ne_110m_admin_0_countries.zip"
    source = map_directory / "source.json"
    shape = map_directory / "ne_110m_admin_0_countries.shp"
    try:
        metadata = json.loads(source.read_text(encoding="utf-8"))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        ok = shape.is_file() and metadata.get("sha256") == digest
        checks.append(("Map data", ok, f"Natural Earth {metadata.get('version')}; {digest}"))
    except Exception as exc:
        checks.append(("Map data", False, str(exc)))
    return checks
