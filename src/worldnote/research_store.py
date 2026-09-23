from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from .db import utc_now


def persist_research(
    connection: sqlite3.Connection,
    topic_row_id: int,
    provider: str,
    package: dict[str, Any],
    audit: dict[str, Any],
    output_dir: str,
) -> str:
    if package.get("fixture_mode"):
        raise ValueError("fixture 研究包不得写入正式研究数据库")
    if not audit.get("passed"):
        raise ValueError("未通过审计的研究包不得写入正式研究数据库")
    run_id = str(uuid.uuid4())
    now = utc_now()
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO research_runs(
                run_id, topic_id, provider, status, research_date, output_dir,
                error, started_at, finished_at
            ) VALUES (?, ?, ?, 'passed', ?, ?, NULL, ?, ?)
            """,
            (run_id, topic_row_id, provider, package["research_date"], output_dir, now, now),
        )
        research_run_id = cursor.lastrowid
        source_db_ids: dict[str, int] = {}
        for source in package["sources"]:
            source_cursor = connection.execute(
                """
                INSERT INTO research_sources(
                    research_run_id, source_key, url, final_url, title, publisher,
                    source_type, reliability_tier, accessed_at, published_at,
                    applicable_at, content_sha256, cache_path, limitations
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_run_id, source["source_id"], source["url"],
                    source["final_url"], source["title"], source["publisher"],
                    source["source_type"], source["reliability_tier"],
                    source["accessed_at"], source["published_at"],
                    source["applicable_at"], source["content_sha256"],
                    source["cache_path"], source["limitations"],
                ),
            )
            source_db_ids[source["source_id"]] = source_cursor.lastrowid
        semantic = {item["claim_id"]: item for item in audit["semantic_audit"]["claim_reviews"]}
        for claim in package["claims"]:
            claim_cursor = connection.execute(
                """
                INSERT INTO research_claims(
                    research_run_id, claim_key, statement, claim_type, importance,
                    volatility, as_of, audit_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_run_id, claim["claim_id"], claim["statement"],
                    claim["claim_type"], claim["importance"], claim["volatility"],
                    claim["as_of"], semantic[claim["claim_id"]]["status"],
                ),
            )
            for ref in claim["source_refs"]:
                connection.execute(
                    """
                    INSERT INTO research_claim_sources(
                        claim_id, source_id, locator, evidence_quote, relation
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        claim_cursor.lastrowid, source_db_ids[ref["source_id"]],
                        ref["locator"], ref["evidence_quote"], ref["relation"],
                    ),
                )
        for evidence in package["location"]["coordinate_evidence"]:
            connection.execute(
                """
                INSERT INTO coordinate_evidence(
                    research_run_id, source_id, latitude, longitude, locator
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    research_run_id, source_db_ids[evidence["source_id"]],
                    evidence["latitude"], evidence["longitude"], evidence["locator"],
                ),
            )
        connection.execute(
            "UPDATE locations SET latitude=?, longitude=?, coordinate_status='verified', "
            "updated_at=? WHERE id=(SELECT location_id FROM topic_candidates WHERE id=?)",
            (
                package["location"]["latitude"], package["location"]["longitude"],
                now, topic_row_id,
            ),
        )
    return run_id
