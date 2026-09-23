from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import utc_now
from .similarity import normalize_name


class SeedError(ValueError):
    pass


def _required(record: dict[str, Any], keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if record.get(key) in (None, "", [])]
    if missing:
        raise SeedError(f"{context} 缺少字段：{', '.join(missing)}")


def load_seed_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("locations"), list):
        raise SeedError("种子文件缺少 locations 数组")
    return payload


def import_seeds(connection: sqlite3.Connection, path: Path) -> int:
    payload = load_seed_file(path)
    checked_on = payload.get("checked_on")
    if not checked_on:
        raise SeedError("种子文件缺少统一核对日期 checked_on")

    imported = 0
    with connection:
        for location in payload["locations"]:
            _required(
                location,
                (
                    "slug",
                    "name_zh",
                    "name_en",
                    "country_code",
                    "country_zh",
                    "country_en",
                    "region",
                    "continent",
                    "location_type",
                    "coordinate_status",
                    "topic",
                ),
                "地点",
            )
            topic = location["topic"]
            _required(
                topic,
                (
                    "id",
                    "hook_zh",
                    "angle_zh",
                    "themes",
                    "visual_tags",
                    "visual_value",
                    "knowledge_value",
                    "story_value",
                    "time_sensitivity",
                    "source",
                ),
                f"地点 {location['slug']} 的选题",
            )
            source = topic["source"]
            _required(
                source,
                ("url", "title", "publisher", "purpose"),
                f"选题 {topic['id']} 的来源",
            )
            now = utc_now()
            connection.execute(
                """
                INSERT INTO locations(
                    slug, name_zh, name_en, country_code, country_zh, country_en,
                    region, continent, location_type, latitude, longitude,
                    coordinate_status, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name_zh=excluded.name_zh, name_en=excluded.name_en,
                    country_code=excluded.country_code, country_zh=excluded.country_zh,
                    country_en=excluded.country_en, region=excluded.region,
                    continent=excluded.continent, location_type=excluded.location_type,
                    latitude=excluded.latitude, longitude=excluded.longitude,
                    coordinate_status=excluded.coordinate_status, active=1,
                    updated_at=excluded.updated_at
                """,
                (
                    location["slug"],
                    location["name_zh"],
                    location["name_en"],
                    location["country_code"],
                    location["country_zh"],
                    location["country_en"],
                    location["region"],
                    location["continent"],
                    location["location_type"],
                    location.get("latitude"),
                    location.get("longitude"),
                    location["coordinate_status"],
                    now,
                    now,
                ),
            )
            location_id = connection.execute(
                "SELECT id FROM locations WHERE slug = ?", (location["slug"],)
            ).fetchone()["id"]

            aliases = {
                location["name_zh"],
                location["name_en"],
                location["slug"],
                *location.get("aliases", []),
            }
            for alias in aliases:
                normalized = normalize_name(alias)
                conflict = connection.execute(
                    "SELECT location_id, alias FROM location_aliases "
                    "WHERE alias_normalized = ?",
                    (normalized,),
                ).fetchone()
                if conflict and conflict["location_id"] != location_id:
                    raise SeedError(
                        f"别名冲突：{alias} 已属于另一个地点（已有别名 {conflict['alias']}）"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO location_aliases"
                    "(location_id, alias, alias_normalized) VALUES (?, ?, ?)",
                    (location_id, alias, normalized),
                )

            connection.execute(
                """
                INSERT INTO topic_candidates(
                    topic_id, location_id, hook_zh, angle_zh, visual_value,
                    knowledge_value, story_value, time_sensitivity, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(topic_id) DO UPDATE SET
                    location_id=excluded.location_id, hook_zh=excluded.hook_zh,
                    angle_zh=excluded.angle_zh, visual_value=excluded.visual_value,
                    knowledge_value=excluded.knowledge_value,
                    story_value=excluded.story_value,
                    time_sensitivity=excluded.time_sensitivity, active=1,
                    updated_at=excluded.updated_at
                """,
                (
                    topic["id"],
                    location_id,
                    topic["hook_zh"],
                    topic["angle_zh"],
                    topic["visual_value"],
                    topic["knowledge_value"],
                    topic["story_value"],
                    topic["time_sensitivity"],
                    now,
                    now,
                ),
            )
            topic_row_id = connection.execute(
                "SELECT id FROM topic_candidates WHERE topic_id = ?", (topic["id"],)
            ).fetchone()["id"]
            connection.execute("DELETE FROM topic_tags WHERE topic_id = ?", (topic_row_id,))
            for tag_type, values in (
                ("theme", topic["themes"]),
                ("visual", topic["visual_tags"]),
            ):
                for value in values:
                    connection.execute(
                        "INSERT INTO topic_tags(topic_id, tag_type, tag) VALUES (?, ?, ?)",
                        (topic_row_id, tag_type, value),
                    )
            connection.execute(
                "DELETE FROM candidate_sources WHERE topic_id = ?", (topic_row_id,)
            )
            connection.execute(
                """
                INSERT INTO candidate_sources(
                    topic_id, source_url, source_title, publisher, checked_on, purpose
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_row_id,
                    source["url"],
                    source["title"],
                    source["publisher"],
                    checked_on,
                    source["purpose"],
                ),
            )
            imported += 1
    return imported

