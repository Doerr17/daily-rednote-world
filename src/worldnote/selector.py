from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .db import utc_now
from .similarity import set_similarity, text_similarity


class SelectionError(ValueError):
    pass


@dataclass(frozen=True)
class Selection:
    topic: dict[str, Any]
    score: float
    duplicate_score: float
    details: dict[str, float]


def _stable_jitter(day: str, topic_id: str, seed: str) -> float:
    digest = hashlib.sha256(f"{day}:{seed}:{topic_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / (2**32 - 1) * 0.1


def _candidate_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT t.*, l.slug, l.name_zh, l.name_en, l.country_code, l.country_zh,
               l.country_en, l.region, l.continent, l.location_type
        FROM topic_candidates t
        JOIN locations l ON l.id = t.location_id
        WHERE t.active = 1 AND l.active = 1
          AND NOT EXISTS (
              SELECT 1 FROM selections s
              JOIN topic_candidates used ON used.id = s.topic_id
              WHERE used.location_id = l.id AND s.status != 'failed'
          )
        ORDER BY t.topic_id
        """
    ).fetchall()
    return [_with_tags(connection, dict(row)) for row in rows]


def get_topic_by_id(connection: sqlite3.Connection, topic_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT t.*, l.slug, l.name_zh, l.name_en, l.country_code, l.country_zh,
               l.country_en, l.region, l.continent, l.location_type
        FROM topic_candidates t
        JOIN locations l ON l.id = t.location_id
        WHERE t.topic_id = ? AND t.active = 1 AND l.active = 1
        """,
        (topic_id,),
    ).fetchone()
    if row is None:
        return None
    topic = _with_tags(connection, dict(row))
    topic["aliases"] = [
        item["alias"]
        for item in connection.execute(
            "SELECT alias FROM location_aliases WHERE location_id=? ORDER BY alias",
            (topic["location_id"],),
        )
    ]
    topic["starter_sources"] = [
        dict(item)
        for item in connection.execute(
            """
            SELECT source_url AS url, source_title AS title, publisher, checked_on, purpose
            FROM candidate_sources WHERE topic_id=? ORDER BY source_url
            """,
            (topic["id"],),
        )
    ]
    return topic


def _with_tags(connection: sqlite3.Connection, topic: dict[str, Any]) -> dict[str, Any]:
    tags = connection.execute(
        "SELECT tag_type, tag FROM topic_tags WHERE topic_id = ? ORDER BY tag",
        (topic["id"],),
    ).fetchall()
    topic["themes"] = [row["tag"] for row in tags if row["tag_type"] == "theme"]
    topic["visual_tags"] = [
        row["tag"] for row in tags if row["tag_type"] == "visual"
    ]
    return topic


def recent_history(connection: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT s.selected_on, s.status, t.id, t.topic_id, t.hook_zh, t.angle_zh,
               l.slug, l.name_zh, l.country_code, l.country_zh, l.region,
               l.continent, l.location_type
        FROM selections s
        JOIN topic_candidates t ON t.id = s.topic_id
        JOIN locations l ON l.id = t.location_id
        WHERE s.status != 'failed'
        ORDER BY s.selected_on DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_with_tags(connection, dict(row)) for row in rows]


def _score(
    topic: dict[str, Any],
    history: list[dict[str, Any]],
    day: str,
    seed: str,
    weights: dict[str, float],
) -> Selection:
    quality = (
        topic["visual_value"] + topic["knowledge_value"] + topic["story_value"]
    ) / 3
    text_scores = [
        text_similarity(
            f"{topic['hook_zh']} {topic['angle_zh']}",
            f"{item['hook_zh']} {item['angle_zh']}",
        )
        for item in history
    ]
    theme_scores = [set_similarity(topic["themes"], item["themes"]) for item in history]
    visual_scores = [
        set_similarity(topic["visual_tags"], item["visual_tags"]) for item in history
    ]
    text_max = max(text_scores, default=0.0)
    theme_max = max(theme_scores, default=0.0)
    visual_max = max(visual_scores, default=0.0)
    duplicate_score = max(text_max, theme_max, visual_max)

    same_country = sum(item["country_code"] == topic["country_code"] for item in history[:6])
    same_region = sum(item["region"] == topic["region"] for item in history[:4])
    same_continent_immediate = bool(history and history[0]["continent"] == topic["continent"])
    same_continent_recent = sum(
        item["continent"] == topic["continent"] for item in history[1:4]
    )
    same_type_immediate = bool(
        history and history[0]["location_type"] == topic["location_type"]
    )
    same_type_recent = sum(
        item["location_type"] == topic["location_type"] for item in history[1:4]
    )

    details = {
        "quality": quality * weights["quality"],
        "same_country_penalty": same_country * weights["same_country"],
        "same_region_penalty": same_region * weights["same_region"],
        "same_continent_penalty": (
            float(same_continent_immediate) * weights["same_continent_immediate"]
            + same_continent_recent * weights["same_continent_recent"]
        ),
        "same_type_penalty": (
            float(same_type_immediate) * weights["same_type_immediate"]
            + same_type_recent * weights["same_type_recent"]
        ),
        "theme_overlap_penalty": theme_max * weights["theme_overlap"],
        "visual_overlap_penalty": visual_max * weights["visual_overlap"],
        "text_similarity_penalty": text_max * weights["text_similarity"],
        "stable_jitter": _stable_jitter(day, topic["topic_id"], seed),
    }
    score = details["quality"] + details["stable_jitter"] - sum(
        value for key, value in details.items() if key.endswith("_penalty")
    )
    return Selection(topic=topic, score=score, duplicate_score=duplicate_score, details=details)


def choose_topic(
    connection: sqlite3.Connection,
    day: str,
    seed: str,
    history_limit: int,
    hard_similarity_threshold: float,
    weights: dict[str, float],
) -> Selection:
    candidates = _candidate_rows(connection)
    if not candidates:
        raise SelectionError("没有未使用的候选地点；请先补充并核对种子库")
    history = recent_history(connection, history_limit)
    ranked = [_score(topic, history, day, seed, weights) for topic in candidates]
    eligible = [item for item in ranked if item.duplicate_score < hard_similarity_threshold]
    if not eligible:
        lowest = min(item.duplicate_score for item in ranked)
        raise SelectionError(
            "所有候选与近期内容过于相似"
            f"（最低重复度 {lowest:.2f}，阈值 {hard_similarity_threshold:.2f}）"
        )
    return max(eligible, key=lambda item: (item.score, item.topic["topic_id"]))


def record_selection(connection: sqlite3.Connection, day: str, selection: Selection) -> None:
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO selections(
                    selected_on, topic_id, status, score, duplicate_score,
                    score_details_json, created_at
                ) VALUES (?, ?, 'selected', ?, ?, ?, ?)
                """,
                (
                    day,
                    selection.topic["id"],
                    selection.score,
                    selection.duplicate_score,
                    json.dumps(selection.details, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise SelectionError(f"{day} 已有选题记录，不会自动覆盖") from exc
