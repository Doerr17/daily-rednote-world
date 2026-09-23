from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

from .db import utc_now


def recent_content_texts(connection: sqlite3.Connection, limit: int) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT f.fingerprint_type, f.text_value
        FROM content_fingerprints f
        JOIN content_runs r ON r.id=f.content_run_id
        WHERE r.status='passed'
        ORDER BY r.content_date DESC, r.id DESC
        LIMIT ?
        """,
        (limit * 4,),
    ).fetchall()
    return [dict(row) for row in rows]


def content_text_groups(draft: dict[str, Any]) -> dict[str, str]:
    titles = "\n".join(item["text"] for item in draft["title_options"])
    caption = "\n".join(item["text"] for item in draft["caption"]["paragraphs"])
    cards = "\n".join(
        " ".join([card["heading"]] + [block["text"] for block in card["blocks"]])
        for card in draft["cards"]
    )
    structure = json.dumps(
        {
            "roles": [card["role"] for card in draft["cards"]],
            "tone_keywords": draft["narrative_strategy"]["tone_keywords"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {"title": titles, "caption": caption, "cards": cards, "structure": structure}


def persist_content(
    connection: sqlite3.Connection,
    topic_row_id: int,
    provider: str,
    package: dict[str, Any],
    draft: dict[str, Any],
    audit: dict[str, Any],
    output_dir: str,
    package_sha256: str,
) -> str:
    if package.get("fixture_mode") or draft.get("fixture_mode"):
        raise ValueError("fixture 文案不得写入正式内容数据库")
    if not audit.get("passed"):
        raise ValueError("未通过审计的文案不得写入正式内容数据库")
    research = connection.execute(
        "SELECT id FROM research_runs WHERE topic_id=? AND status='passed' "
        "ORDER BY finished_at DESC, id DESC LIMIT 1",
        (topic_row_id,),
    ).fetchone()
    if research is None:
        raise ValueError("找不到对应的已通过研究运行")
    run_id = str(uuid.uuid4())
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO content_runs(
                run_id, research_run_id, topic_id, provider, status, content_date,
                output_dir, package_sha256, created_at
            ) VALUES (?, ?, ?, ?, 'passed', ?, ?, ?, ?)
            """,
            (
                run_id, research["id"], topic_row_id, provider,
                package["research_date"], output_dir, package_sha256, utc_now(),
            ),
        )
        for kind, value in content_text_groups(draft).items():
            connection.execute(
                """
                INSERT INTO content_fingerprints(
                    content_run_id, fingerprint_type, text_value, fingerprint
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid, kind, value,
                    hashlib.sha256(value.encode("utf-8")).hexdigest(),
                ),
            )
    return run_id
