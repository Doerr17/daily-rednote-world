from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def _banner(fixture: bool) -> list[str]:
    return ["> **TEST FIXTURE ONLY：以下内容仅用于验证程序，禁止发布。**", ""] if fixture else []


def _content_markdown(draft: dict[str, Any]) -> str:
    lines = ["# 发布素材", "", "## 标题候选", ""] + _banner(draft["fixture_mode"])
    for index, item in enumerate(draft["title_options"], 1):
        lines.append(f"{index}. {item['text']}")
    lines.extend(["", "## 发布正文", ""])
    for item in draft["caption"]["paragraphs"]:
        lines.extend([item["text"], ""])
    lines.extend([" ".join(draft["caption"]["hashtags"]), ""])
    return "\n".join(lines).rstrip() + "\n"


def _learning_markdown(draft: dict[str, Any]) -> str:
    note = draft["learning_notes"]
    lines = ["# 个人学习笔记", ""] + _banner(draft["fixture_mode"])
    lines.extend(
        [
            note["overview"]["text"],
            "",
            f"证据：`{' '.join(note['overview']['claim_refs'])}`",
            "",
        ]
    )
    for section in note["sections"]:
        lines.extend([f"## {section['heading']}", ""])
        for item in section["items"]:
            lines.append(f"- {item['text']} `{' '.join(item['claim_refs'])}`")
        lines.append("")
    lines.extend(["## 未知与限制", ""])
    lines.extend(f"- {item}" for item in note["unknowns"])
    lines.extend(["", "## 值得继续探索", ""])
    lines.extend(f"- {item}" for item in note["further_questions"])
    return "\n".join(lines).rstrip() + "\n"


def knowledge_markdown(package: dict[str, Any], draft: dict[str, Any]) -> str:
    location = package["location"]
    lines = [f"# {location['name_zh']} / {location['name_en']}", ""] + _banner(package["fixture_mode"])
    lines.extend(
        [
            f"- 国家：{location['country_zh']} / {location['country_en']}",
            f"- 地区：{location['region']}",
            f"- 洲：{location['continent']}",
            f"- 类型：{location['location_type']}",
            f"- 坐标：{location['latitude']}, {location['longitude']}（{location['coordinate_status']}）",
            "",
            "## 地理、自然、历史与文化知识",
            "",
        ]
    )
    for claim in package["claims"]:
        lines.append(f"- **{claim['claim_id']} · {claim['claim_type']}**：{claim['statement']}")
    lines.extend(["", "## 参考资料", ""])
    for source in package["sources"]:
        lines.append(f"- [{source['title']}]({source['final_url']}) — {source['publisher']}，核对于 {source['accessed_at']}")
    lines.extend(["", "## 未知与限制", ""])
    lines.extend(f"- {item}" for item in package["unknowns"])
    lines.extend(["", "## 值得进一步探索的问题", ""])
    lines.extend(f"- {item}" for item in package["further_questions"])
    lines.extend(["", "## 本次内容策略", "", f"- {draft['narrative_strategy']['angle']}"])
    return "\n".join(lines).rstrip() + "\n"


def write_content_artifacts(
    destination: Path,
    package: dict[str, Any],
    draft: dict[str, Any],
    audit: dict[str, Any],
    research_sha256: str,
    force: bool = False,
) -> tuple[Path, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        raise FileExistsError(f"输出目录已存在：{destination}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        content_text = json.dumps(draft, ensure_ascii=False, indent=2) + "\n"
        audit_to_write = dict(audit)
        audit_to_write["research_package_sha256"] = research_sha256
        content_markdown = _content_markdown(draft)
        (temporary / "content.md").write_text(content_markdown, encoding="utf-8")
        (temporary / "learning_notes.md").write_text(_learning_markdown(draft), encoding="utf-8")
        (temporary / "knowledge_entry.md").write_text(
            knowledge_markdown(package, draft), encoding="utf-8"
        )
        (temporary / "cards.json").write_text(
            json.dumps(draft["cards"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (temporary / "content_package.json").write_text(content_text, encoding="utf-8")
        content_sha256 = hashlib.sha256(
            (temporary / "content_package.json").read_bytes()
        ).hexdigest()
        audit_to_write["content_package_sha256"] = content_sha256
        (temporary / "content_audit.json").write_text(
            json.dumps(audit_to_write, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        return destination, content_sha256
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
