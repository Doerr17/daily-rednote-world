from __future__ import annotations

from itertools import combinations
from typing import Any

from .research_validation import haversine_km, normalize_evidence


def audit_research(
    package: dict[str, Any],
    source_texts: dict[str, str],
    semantic_audit: dict[str, Any],
    coordinate_tolerance_km: float,
    minimum_sources: int,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    source_by_id = {source["source_id"]: source for source in package["sources"]}
    claim_by_id = {claim["claim_id"]: claim for claim in package["claims"]}

    if len(source_by_id) < minimum_sources:
        failures.append(f"来源不足：{len(source_by_id)}，最低要求 {minimum_sources}")

    deterministic_claims: list[dict[str, Any]] = []
    for claim in package["claims"]:
        claim_failures: list[str] = []
        supporting_sources: set[str] = set()
        for ref in claim["source_refs"]:
            source = source_by_id.get(ref["source_id"])
            if source is None:
                claim_failures.append(f"未知来源 {ref['source_id']}")
                continue
            haystack = normalize_evidence(source_texts.get(ref["source_id"], ""))
            needle = normalize_evidence(ref["evidence_quote"])
            if not needle or needle not in haystack:
                claim_failures.append(
                    f"证据摘录未在缓存正文中找到：{ref['source_id']} / {ref['locator']}"
                )
            if ref["relation"] == "supports":
                supporting_sources.add(ref["source_id"])
            if claim["claim_type"] == "legend" and ref["relation"] == "supports":
                claim_failures.append("民间传说不得标为普通事实支持")
        if claim["importance"] == "core":
            eligible = [
                source_id
                for source_id in supporting_sources
                if source_by_id[source_id]["reliability_tier"] != "discovery_only"
            ]
            if not eligible:
                claim_failures.append("核心主张没有非 discovery_only 来源直接支持")
        if claim["volatility"] == "time_sensitive" and not claim.get("as_of"):
            claim_failures.append("易变主张缺少 as_of")
        if claim["claim_type"] in {"tourism_promotion", "unknown"} and claim["importance"] == "core":
            claim_failures.append("宣传或未知项不能作为核心主张")
        failures.extend(f"{claim['claim_id']}: {message}" for message in claim_failures)
        deterministic_claims.append(
            {
                "claim_id": claim["claim_id"],
                "passed": not claim_failures,
                "issues": claim_failures,
                "supporting_source_ids": sorted(supporting_sources),
            }
        )

    coordinate_evidence = package["location"]["coordinate_evidence"]
    for item in coordinate_evidence:
        source_text = normalize_evidence(source_texts.get(item["source_id"], ""))
        quote = normalize_evidence(item["evidence_quote"])
        if not quote or quote not in source_text:
            failures.append(
                f"坐标证据摘录未在缓存正文中找到：{item['source_id']} / {item['locator']}"
            )
    coordinate_publishers = {
        source_by_id[item["source_id"]]["publisher"]
        for item in coordinate_evidence
        if item["source_id"] in source_by_id
    }
    pair_distances = [
        haversine_km(
            float(left["latitude"]),
            float(left["longitude"]),
            float(right["latitude"]),
            float(right["longitude"]),
        )
        for left, right in combinations(coordinate_evidence, 2)
    ]
    target_distances = [
        haversine_km(
            float(package["location"]["latitude"]),
            float(package["location"]["longitude"]),
            float(item["latitude"]),
            float(item["longitude"]),
        )
        for item in coordinate_evidence
    ]
    max_pair_distance = max(pair_distances, default=float("inf"))
    max_target_distance = max(target_distances, default=float("inf"))
    coordinate_passed = True
    if len(coordinate_evidence) < 2:
        failures.append("坐标证据少于两条")
        coordinate_passed = False
    if len(coordinate_publishers) < 2:
        failures.append("坐标证据不是来自两个独立发布者")
        coordinate_passed = False
    if max_pair_distance > coordinate_tolerance_km:
        failures.append(
            f"坐标来源冲突：最大距离 {max_pair_distance:.2f} km，"
            f"容差 {coordinate_tolerance_km:.2f} km"
        )
        coordinate_passed = False
    if max_target_distance > coordinate_tolerance_km:
        failures.append(
            f"记录坐标偏离来源：最大距离 {max_target_distance:.2f} km，"
            f"容差 {coordinate_tolerance_km:.2f} km"
        )
        coordinate_passed = False
    if package["location"]["coordinate_status"] != "verified":
        failures.append("coordinate_status 不是 verified")
        coordinate_passed = False

    semantic_reviews = semantic_audit.get("claim_reviews", [])
    semantic_by_id: dict[str, dict[str, Any]] = {}
    for review in semantic_reviews:
        claim_id = review.get("claim_id")
        if claim_id in semantic_by_id:
            failures.append(f"独立审计重复返回主张 {claim_id}")
        semantic_by_id[claim_id] = review
    missing_reviews = set(claim_by_id) - set(semantic_by_id)
    unknown_reviews = set(semantic_by_id) - set(claim_by_id)
    if missing_reviews:
        failures.append(f"独立审计遗漏主张：{sorted(missing_reviews)}")
    if unknown_reviews:
        failures.append(f"独立审计包含未知主张：{sorted(unknown_reviews)}")
    for claim_id, review in semantic_by_id.items():
        if review.get("status") != "supported":
            failures.append(
                f"独立审计未通过 {claim_id}: {review.get('status')} - {review.get('reason', '')}"
            )
    coordinate_review = semantic_audit.get("coordinate_review", {})
    if coordinate_review.get("status") != "supported":
        failures.append(
            "独立坐标审计未通过："
            f"{coordinate_review.get('status', 'missing')} - {coordinate_review.get('reason', '')}"
        )

    for claim_id in package["travel_claim_ids"]:
        claim = claim_by_id[claim_id]
        if claim["volatility"] != "time_sensitive" or not claim.get("as_of"):
            warnings.append(f"旅行主张 {claim_id} 建议标为 time_sensitive 并提供 as_of")

    return {
        "audit_version": "1.0",
        "passed": not failures,
        "fixture_mode": bool(package["fixture_mode"]),
        "critical_failures": failures,
        "warnings": warnings,
        "coordinate_check": {
            "passed": coordinate_passed,
            "independent_publishers": sorted(coordinate_publishers),
            "max_pair_distance_km": None if max_pair_distance == float("inf") else round(max_pair_distance, 4),
            "max_target_distance_km": None if max_target_distance == float("inf") else round(max_target_distance, 4),
            "tolerance_km": coordinate_tolerance_km,
        },
        "deterministic_claim_checks": deterministic_claims,
        "semantic_audit": semantic_audit,
    }
