from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .db import utc_now


def _validate_timestamp(value: str, field: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 ISO 8601 日期或时间") from exc


def publish_run(
    connection: sqlite3.Connection,
    run_date: str,
    published_at: str,
    platform_post_url: str | None,
    confirmed: bool,
) -> int:
    if not confirmed:
        raise ValueError("必须显式提供 --confirm-reviewed 才能登记发布")
    _validate_timestamp(published_at, "published_at")
    run = connection.execute(
        "SELECT id, topic_id, status FROM daily_runs WHERE run_date=?", (run_date,)
    ).fetchone()
    if run is None:
        raise ValueError(f"{run_date} 没有 daily 运行记录")
    if run["status"] == "published":
        raise ValueError(f"{run_date} 已登记发布，不会重复写入")
    if run["status"] != "awaiting_review":
        raise ValueError("只有 awaiting_review 状态可以登记发布")
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO publications(
                daily_run_id, published_at, platform_post_url,
                confirmed_by_human, created_at
            ) VALUES (?, ?, ?, 1, ?)
            """,
            (run["id"], published_at, platform_post_url, utc_now()),
        )
        connection.execute(
            "UPDATE daily_runs SET status='published', error=NULL, updated_at=? WHERE id=?",
            (utc_now(), run["id"]),
        )
        if run["topic_id"] is not None:
            connection.execute(
                "UPDATE selections SET status='published' WHERE selected_on=?",
                (run_date,),
            )
    return int(cursor.lastrowid)


def add_metrics(
    connection: sqlite3.Connection,
    run_date: str,
    as_of: str,
    views: int,
    likes: int,
    saves: int,
    comments: int,
    followers_gained: int,
) -> int:
    values = [views, likes, saves, comments, followers_gained]
    _validate_timestamp(as_of, "as_of")
    if any(value < 0 for value in values):
        raise ValueError("运营数据必须是非负整数")
    publication = connection.execute(
        """
        SELECT p.id FROM publications p
        JOIN daily_runs r ON r.id=p.daily_run_id
        WHERE r.run_date=?
        """,
        (run_date,),
    ).fetchone()
    if publication is None:
        raise ValueError("尚未人工登记发布，不能录入运营数据")
    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO metrics(
                    publication_id, as_of, views, likes, saves, comments,
                    followers_gained, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication["id"], as_of, views, likes, saves, comments,
                    followers_gained, utc_now(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"{run_date} 在 {as_of} 已有运营数据，不会覆盖") from exc
    return int(cursor.lastrowid)


def list_metrics(connection: sqlite3.Connection, run_date: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE r.run_date=?" if run_date else ""
    params = (run_date,) if run_date else ()
    rows = connection.execute(
        f"""
        SELECT r.run_date, p.published_at, p.platform_post_url, m.as_of,
               m.views, m.likes, m.saves, m.comments, m.followers_gained
        FROM metrics m
        JOIN publications p ON p.id=m.publication_id
        JOIN daily_runs r ON r.id=p.daily_run_id
        {where}
        ORDER BY r.run_date DESC, m.as_of DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]
