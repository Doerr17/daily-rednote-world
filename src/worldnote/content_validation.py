from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .similarity import text_similarity


class ContentValidationError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_audited_research(directory: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    package_path = directory / "research_package.json"
    audit_path = directory / "audit.json"
    if not package_path.is_file() or not audit_path.is_file():
        raise ContentValidationError("研究目录缺少 research_package.json 或 audit.json")
    package_bytes = package_path.read_bytes()
    package = json.loads(package_bytes.decode("utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    digest = _sha256(package_bytes)
    if not audit.get("passed"):
        raise ContentValidationError("研究包未通过事实审计")
    if audit.get("research_package_sha256") != digest:
        raise ContentValidationError("研究包哈希与审计记录不一致，禁止写作")
    return package, audit, digest


def _referenced_units(draft: dict[str, Any]) -> Iterable[tuple[str, str, list[str]]]:
    for index, item in enumerate(draft.get("title_options", []), 1):
        yield f"title_options[{index}]", item.get("text", ""), item.get("claim_refs", [])
    for index, item in enumerate(draft.get("caption", {}).get("paragraphs", []), 1):
        yield f"caption.paragraphs[{index}]", item.get("text", ""), item.get("claim_refs", [])
    for card in draft.get("cards", []):
        yield (
            f"cards[{card.get('number')}].heading",
            card.get("heading", ""),
            card.get("heading_claim_refs", []),
        )
        for index, item in enumerate(card.get("blocks", []), 1):
            yield f"cards[{card.get('number')}].blocks[{index}]", item.get("text", ""), item.get("claim_refs", [])
    note = draft.get("learning_notes", {})
    overview = note.get("overview", {})
    yield "learning_notes.overview", overview.get("text", ""), overview.get("claim_refs", [])
    for section_index, section in enumerate(note.get("sections", []), 1):
        for item_index, item in enumerate(section.get("items", []), 1):
            yield (
                f"learning_notes.sections[{section_index}].items[{item_index}]",
                item.get("text", ""),
                item.get("claim_refs", []),
            )


def _public_texts(draft: dict[str, Any]) -> list[tuple[str, str]]:
    values = [(path, text) for path, text, _ in _referenced_units(draft)]
    for index, tag in enumerate(draft.get("caption", {}).get("hashtags", []), 1):
        values.append((f"caption.hashtags[{index}]", tag))
    return values


def validate_semantic_content_audit(audit: dict[str, Any]) -> None:
    if audit.get("audit_version") != "1.0":
        raise ContentValidationError("不支持的文案审计版本")
    if not isinstance(audit.get("issues"), list) or not isinstance(
        audit.get("overall_assessment"), str
    ):
        raise ContentValidationError("文案审计结构不完整")
    for issue in audit["issues"]:
        required = {"severity", "code", "location", "message"}
        if set(issue) != required or issue["severity"] not in {"error", "warning"}:
            raise ContentValidationError("文案审计 issue 结构无效")


def audit_content(
    package: dict[str, Any],
    draft: dict[str, Any],
    semantic_audit: dict[str, Any],
    settings: dict[str, Any],
    recent_texts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    validate_semantic_content_audit(semantic_audit)

    if draft.get("schema_version") != "1.0":
        failures.append("不支持的内容草稿版本")
    if draft.get("topic_id") != package.get("topic_id"):
        failures.append("内容草稿 topic_id 与研究包不一致")
    if draft.get("fixture_mode") != package.get("fixture_mode"):
        failures.append("内容草稿 fixture_mode 与研究包不一致")

    claims = {item["claim_id"]: item for item in package.get("claims", [])}
    used_claims: set[str] = set()
    for path, text, refs in _referenced_units(draft):
        if not text.strip():
            failures.append(f"{path} 为空")
        if not refs:
            failures.append(f"{path} 缺少 claim_refs")
            continue
        unknown = sorted(set(refs) - set(claims))
        if unknown:
            failures.append(f"{path} 引用了未知主张：{', '.join(unknown)}")
            continue
        used_claims.update(refs)
        numeric_tokens = set(re.findall(r"\d+(?:\.\d+)?", text))
        supported_numbers = set(
            re.findall(r"\d+(?:\.\d+)?", " ".join(claims[ref]["statement"] for ref in refs))
        )
        unsupported = sorted(numeric_tokens - supported_numbers)
        if unsupported:
            failures.append(f"{path} 含引用主张未支持的数字：{', '.join(unsupported)}")

    core = {item["claim_id"] for item in claims.values() if item["importance"] == "core"}
    missing_core = sorted(core - used_claims)
    if missing_core:
        failures.append(f"公开内容未覆盖核心主张：{', '.join(missing_core)}")

    titles = draft.get("title_options", [])
    if not settings["title_min"] <= len(titles) <= settings["title_max"]:
        failures.append("标题数量超出配置范围")
    title_texts = [item.get("text", "") for item in titles]
    if len(title_texts) != len(set(title_texts)):
        failures.append("标题候选存在完全重复")
    for index, title in enumerate(title_texts, 1):
        if len(title) > settings["title_max_chars"]:
            failures.append(f"title_options[{index}] 超过字符上限")

    cards = draft.get("cards", [])
    if not settings["card_min"] <= len(cards) <= settings["card_max"]:
        failures.append("卡片数量超出配置范围")
    if [item.get("number") for item in cards] != list(range(1, len(cards) + 1)):
        failures.append("卡片编号必须从 1 连续递增")
    roles = [item.get("role") for item in cards]
    if len(roles) != len(set(roles)):
        failures.append("卡片叙事角色不得重复")
    if len(roles) >= 2 and roles[:2] != ["cover", "location"]:
        failures.append("前两张卡必须依次为 cover、location")
    if len(cards) == 6 and roles[-1:] != ["summary"]:
        failures.append("六卡结构的最后一张必须为 summary")
    if len(cards) == 6 and roles != ["cover", "location", "feature", "story", "facts", "summary"]:
        failures.append("六卡结构角色顺序必须为 cover、location、feature、story、facts、summary")
    if len(cards) == 5 and "summary" in roles:
        failures.append("五卡结构不应提前使用 summary")
    for card in cards:
        if len(card.get("heading", "")) > settings["card_heading_max_chars"]:
            failures.append(f"cards[{card.get('number')}].heading 超过字符上限")
        body = "\n".join(block.get("text", "") for block in card.get("blocks", []))
        if len(body) > settings["card_body_max_chars"]:
            failures.append(f"cards[{card.get('number')}] 正文超过字符上限")

    caption = "\n\n".join(
        item.get("text", "") for item in draft.get("caption", {}).get("paragraphs", [])
    )
    if len(caption) > settings["caption_max_chars"]:
        failures.append("发布正文超过字符上限")

    for path, text in _public_texts(draft):
        for phrase in settings["blocked_phrases"]:
            if phrase in text:
                failures.append(f"{path} 命中禁用表达：{phrase}")
        for phrase in settings["false_visit_phrases"]:
            if phrase in text:
                failures.append(f"{path} 命中虚假到访表达：{phrase}")

    note = draft.get("learning_notes", {})
    if note.get("unknowns") != package.get("unknowns"):
        failures.append("学习笔记必须原样保留研究包 unknowns")
    if note.get("further_questions") != package.get("further_questions"):
        failures.append("学习笔记必须原样保留研究包 further_questions")

    card_texts = [
        " ".join([card.get("heading", "")] + [item.get("text", "") for item in card.get("blocks", [])])
        for card in cards
    ]
    threshold = float(settings["within_package_similarity_threshold"])
    for left in range(len(card_texts)):
        for right in range(left + 1, len(card_texts)):
            score = text_similarity(card_texts[left], card_texts[right])
            if score >= threshold:
                failures.append(
                    f"卡片 {left + 1} 与 {right + 1} 重复度 {score:.2f} 达到阈值 {threshold:.2f}"
                )

    for group_name, values in (
        ("标题", title_texts),
        ("正文段落", [item.get("text", "") for item in draft.get("caption", {}).get("paragraphs", [])]),
    ):
        for left in range(len(values)):
            for right in range(left + 1, len(values)):
                score = text_similarity(values[left], values[right])
                if score >= threshold:
                    warnings.append(
                        f"{group_name} {left + 1} 与 {right + 1} 重复度 {score:.2f} 达到阈值 {threshold:.2f}"
                    )

    recent_threshold = float(settings["recent_similarity_threshold"])
    current_groups = {
        "title": "\n".join(title_texts),
        "caption": caption,
        "cards": "\n".join(card_texts),
        "structure": json.dumps(
            {
                "roles": roles,
                "tone_keywords": draft.get("narrative_strategy", {}).get("tone_keywords", []),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }
    for recent in recent_texts or []:
        kind = recent.get("fingerprint_type", "")
        if kind not in current_groups:
            continue
        score = text_similarity(current_groups[kind], recent.get("text_value", ""))
        if kind == "structure" and score >= recent_threshold:
            warnings.append(
                f"叙事结构/语气关键词与近期内容重复度 {score:.2f}，建议人工复核"
            )
        elif score >= recent_threshold:
            failures.append(
                f"{kind} 与近期内容重复度 {score:.2f} 达到阈值 {recent_threshold:.2f}"
            )

    for issue in semantic_audit["issues"]:
        message = f"语义审计 {issue['code']} @ {issue['location']}：{issue['message']}"
        (failures if issue["severity"] == "error" else warnings).append(message)

    return {
        "audit_version": "1.0",
        "passed": not failures,
        "critical_failures": failures,
        "warnings": warnings,
        "semantic_audit": semantic_audit,
    }
