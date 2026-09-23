from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


class ResearchValidationError(ValueError):
    pass


SOURCE_TYPES = {
    "government", "unesco", "museum", "academic", "transport",
    "tourism_official", "reference", "fixture",
}
RELIABILITY_TIERS = {"primary", "authoritative", "secondary", "discovery_only"}
CLAIM_TYPES = {"fact", "interpretation", "legend", "tourism_promotion", "unknown"}
RELATIONS = {"supports", "contradicts", "context_only", "reported_as_legend"}


def _require(record: dict[str, Any], keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if key not in record or record[key] == ""]
    if missing:
        raise ResearchValidationError(f"{context} 缺少字段：{', '.join(missing)}")


def validate_schema_documents(schema_dir: Path) -> None:
    required = {
        "location.schema.json", "source.schema.json", "claim.schema.json",
        "research_package.schema.json", "research_draft.schema.json",
        "research_audit.schema.json",
        "content_draft.schema.json", "content_audit.schema.json",
        "media_candidate.schema.json", "media_manifest.schema.json",
    }
    missing = sorted(name for name in required if not (schema_dir / name).is_file())
    if missing:
        raise ResearchValidationError(f"缺少 Schema：{', '.join(missing)}")
    for name in required:
        json.loads((schema_dir / name).read_text(encoding="utf-8"))


def validate_draft(package: dict[str, Any]) -> None:
    _require(
        package,
        (
            "schema_version", "research_date", "topic_id", "fixture_mode", "location",
            "sources", "claims", "interesting_fact_claim_ids", "uniqueness_claim_ids",
            "travel_claim_ids", "unknowns", "further_questions",
        ),
        "研究包",
    )
    if package["schema_version"] != "1.0":
        raise ResearchValidationError("不支持的研究包版本")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", package["research_date"]):
        raise ResearchValidationError("research_date 必须为 YYYY-MM-DD")
    if len(package["sources"]) < 2:
        raise ResearchValidationError("研究包至少需要两个来源")
    if len(package["claims"]) < 3:
        raise ResearchValidationError("研究包至少需要三条主张")

    location = package["location"]
    _require(
        location,
        (
            "name_zh", "name_en", "country_zh", "country_en", "region", "continent",
            "location_type", "latitude", "longitude", "coordinate_status",
            "coordinate_evidence",
        ),
        "地点",
    )
    if not -90 <= float(location["latitude"]) <= 90:
        raise ResearchValidationError("纬度超出范围")
    if not -180 <= float(location["longitude"]) <= 180:
        raise ResearchValidationError("经度超出范围")
    if len(location["coordinate_evidence"]) < 2:
        raise ResearchValidationError("坐标至少需要两个证据记录")

    source_ids: set[str] = set()
    for source in package["sources"]:
        _require(
            source,
            (
                "source_id", "url", "title", "publisher", "source_type",
                "reliability_tier", "language", "published_at", "applicable_at",
                "limitations",
            ),
            "来源",
        )
        if source["source_id"] in source_ids:
            raise ResearchValidationError(f"来源 ID 重复：{source['source_id']}")
        if source["source_type"] not in SOURCE_TYPES:
            raise ResearchValidationError(f"未知来源类型：{source['source_type']}")
        if source["reliability_tier"] not in RELIABILITY_TIERS:
            raise ResearchValidationError(f"未知可靠性层级：{source['reliability_tier']}")
        source_ids.add(source["source_id"])

    claim_ids: set[str] = set()
    for claim in package["claims"]:
        _require(
            claim,
            (
                "claim_id", "statement", "claim_type", "importance", "volatility",
                "as_of", "source_refs",
            ),
            "主张",
        )
        if claim["claim_id"] in claim_ids:
            raise ResearchValidationError(f"主张 ID 重复：{claim['claim_id']}")
        if claim["claim_type"] not in CLAIM_TYPES:
            raise ResearchValidationError(f"未知主张类型：{claim['claim_type']}")
        if claim["importance"] not in {"core", "supporting"}:
            raise ResearchValidationError("主张 importance 必须为 core 或 supporting")
        if claim["volatility"] not in {"stable", "time_sensitive"}:
            raise ResearchValidationError("未知时效类型")
        if claim["volatility"] == "time_sensitive" and not claim["as_of"]:
            raise ResearchValidationError(f"易变主张 {claim['claim_id']} 缺少 as_of")
        if not claim["source_refs"]:
            raise ResearchValidationError(f"主张 {claim['claim_id']} 没有证据引用")
        for ref in claim["source_refs"]:
            _require(ref, ("source_id", "locator", "evidence_quote", "relation"), "证据引用")
            if ref["source_id"] not in source_ids:
                raise ResearchValidationError(
                    f"主张 {claim['claim_id']} 引用了未知来源 {ref['source_id']}"
                )
            if ref["relation"] not in RELATIONS:
                raise ResearchValidationError(f"未知证据关系：{ref['relation']}")
        claim_ids.add(claim["claim_id"])

    for field in ("interesting_fact_claim_ids", "uniqueness_claim_ids", "travel_claim_ids"):
        unknown = set(package[field]) - claim_ids
        if unknown:
            raise ResearchValidationError(f"{field} 引用了未知主张：{sorted(unknown)}")
    if len(package["interesting_fact_claim_ids"]) != 3:
        raise ResearchValidationError("interesting_fact_claim_ids 必须恰好有三项")
    for evidence in location["coordinate_evidence"]:
        _require(
            evidence,
            ("source_id", "latitude", "longitude", "locator", "evidence_quote"),
            "坐标证据",
        )
        if evidence["source_id"] not in source_ids:
            raise ResearchValidationError(f"坐标引用了未知来源 {evidence['source_id']}")


def validate_semantic_audit(audit: dict[str, Any]) -> None:
    _require(audit, ("audit_version", "claim_reviews", "coordinate_review", "overall_notes"), "独立审计")
    if audit["audit_version"] != "1.0":
        raise ResearchValidationError("不支持的独立审计版本")
    seen: set[str] = set()
    for review in audit["claim_reviews"]:
        _require(review, ("claim_id", "status", "reason", "verified_source_ids"), "主张审计")
        if review["claim_id"] in seen:
            raise ResearchValidationError(f"独立审计重复主张 {review['claim_id']}")
        if review["status"] not in {"supported", "unsupported", "conflicted", "needs_review"}:
            raise ResearchValidationError(f"未知审计状态：{review['status']}")
        seen.add(review["claim_id"])
    _require(audit["coordinate_review"], ("status", "reason"), "坐标审计")
    if audit["coordinate_review"]["status"] not in {"supported", "conflicted", "needs_review"}:
        raise ResearchValidationError("未知坐标审计状态")


def normalize_evidence(value: str) -> str:
    return " ".join(value.casefold().split())


def haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    radius = 6371.0088
    a_lat_r, b_lat_r = math.radians(a_lat), math.radians(b_lat)
    delta_lat = math.radians(b_lat - a_lat)
    delta_lon = math.radians(b_lon - a_lon)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(a_lat_r) * math.cos(b_lat_r) * math.sin(delta_lon / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
