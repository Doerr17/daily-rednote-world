from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def _claim_map(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {claim["claim_id"]: claim for claim in package["claims"]}


def _research_markdown(package: dict[str, Any]) -> str:
    location = package["location"]
    claims = _claim_map(package)
    lines = ["# 地点研究账本", ""]
    if package["fixture_mode"]:
        lines.extend(["> **TEST FIXTURE ONLY：以下内容仅用于验证程序，禁止发布。**", ""])
    lines.extend(
        [
            f"- 研究日期：{package['research_date']}",
            f"- 地点：{location['name_zh']} / {location['name_en']}",
            f"- 国家与地区：{location['country_zh']}，{location['region']}，{location['continent']}",
            f"- 类型：{location['location_type']}",
            f"- 坐标：{location['latitude']}, {location['longitude']}（{location['coordinate_status']}）",
            "",
            "## 经过分类的主张",
            "",
        ]
    )
    for claim in package["claims"]:
        lines.extend(
            [
                f"### {claim['claim_id']} · {claim['claim_type']} · {claim['importance']}",
                "",
                claim["statement"],
                "",
                f"- 时效：{claim['volatility']}" + (f"；截至 {claim['as_of']}" if claim.get("as_of") else ""),
                "- 证据：" + "；".join(
                    f"{ref['source_id']}（{ref['locator']}，{ref['relation']}）"
                    for ref in claim["source_refs"]
                ),
                "",
            ]
        )
    lines.extend(["## 三个有趣事实", ""])
    lines.extend(f"- {claims[claim_id]['statement']}" for claim_id in package["interesting_fact_claim_ids"])
    lines.extend(["", "## 真正独特之处", ""])
    lines.extend(f"- {claims[claim_id]['statement']}" for claim_id in package["uniqueness_claim_ids"])
    lines.extend(["", "## 未知与边界", ""])
    lines.extend(f"- {item}" for item in package["unknowns"])
    lines.extend(["", "## 值得继续探索", ""])
    lines.extend(f"- {item}" for item in package["further_questions"])
    return "\n".join(lines).rstrip() + "\n"


def _sources_markdown(package: dict[str, Any]) -> str:
    lines = ["# 来源账本", ""]
    if package["fixture_mode"]:
        lines.extend(["> **TEST FIXTURE ONLY：fixture URL 不是现实来源。**", ""])
    refs_by_source: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for claim in package["claims"]:
        for ref in claim["source_refs"]:
            refs_by_source.setdefault(ref["source_id"], []).append((claim["claim_id"], ref))
    for evidence in package["location"]["coordinate_evidence"]:
        refs_by_source.setdefault(evidence["source_id"], []).append(
            (
                "coordinate",
                {
                    "locator": evidence["locator"],
                    "relation": "supports",
                    "evidence_quote": evidence["evidence_quote"],
                },
            )
        )
    for source in package["sources"]:
        lines.extend(
            [
                f"## {source['source_id']} · {source['title']}",
                "",
                f"- 发布者：{source['publisher']}",
                f"- 类型/层级：{source['source_type']} / {source['reliability_tier']}",
                f"- 原始链接：<{source['url']}>",
                f"- 最终链接：<{source['final_url']}>",
                f"- 核对时间：{source['accessed_at']}",
                f"- 内容 SHA-256：`{source['content_sha256']}`",
                f"- 缓存：`{source['cache_path']}`",
                f"- 限制：{source['limitations'] or '未记录额外限制'}",
                "- 本文使用的证据：",
            ]
        )
        for claim_id, ref in refs_by_source.get(source["source_id"], []):
            lines.append(
                f"  - {claim_id} · {ref['locator']} · {ref['relation']} · “{ref['evidence_quote']}”"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_research_artifacts(
    destination: Path,
    package: dict[str, Any],
    audit: dict[str, Any],
    force: bool = False,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        raise FileExistsError(f"输出目录已存在：{destination}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        package_text = json.dumps(package, ensure_ascii=False, indent=2) + "\n"
        audit_to_write = dict(audit)
        (temporary / "research.md").write_text(_research_markdown(package), encoding="utf-8")
        (temporary / "sources.md").write_text(_sources_markdown(package), encoding="utf-8")
        (temporary / "location.json").write_text(
            json.dumps(package["location"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (temporary / "research_package.json").write_text(
            package_text,
            encoding="utf-8",
        )
        audit_to_write["research_package_sha256"] = hashlib.sha256(
            (temporary / "research_package.json").read_bytes()
        ).hexdigest()
        (temporary / "audit.json").write_text(
            json.dumps(audit_to_write, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
