from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from .fetcher import FetchError
from .research_artifacts import write_research_artifacts
from .research_audit import audit_research
from .research_validation import (
    ResearchValidationError,
    validate_draft,
    validate_semantic_audit,
)
from .similarity import normalize_name


class ResearchGateError(RuntimeError):
    def __init__(
        self,
        message: str,
        audit: dict[str, Any],
        failure_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.audit = audit
        self.failure_path = failure_path


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build_research_package(
    root: Path,
    provider,
    fetcher,
    topic: dict[str, Any] | None,
    research_date: str,
    coordinate_tolerance_km: float,
    minimum_sources: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    can_retry = callable(getattr(provider, "add_research_feedback", None))
    for attempt in range(3):
        try:
            draft = provider.research(topic, research_date)
            validate_draft(draft)
            if topic:
                if draft["topic_id"] != topic["topic_id"]:
                    raise ResearchValidationError(
                        f"模型返回了错误选题：{draft['topic_id']}，预期 {topic['topic_id']}"
                    )
                accepted_names = {normalize_name(value) for value in topic["aliases"]}
                returned_names = {
                    normalize_name(draft["location"]["name_zh"]),
                    normalize_name(draft["location"]["name_en"]),
                }
                if not accepted_names.intersection(returned_names):
                    raise ResearchValidationError("模型返回的地点名称与候选地点及其别名不一致")
            package = copy.deepcopy(draft)
            source_texts: dict[str, str] = {}
            enriched_sources: list[dict[str, Any]] = []
            for source in draft["sources"]:
                document = fetcher.fetch(source["url"])
                source_texts[source["source_id"]] = document.text
                enriched = dict(source)
                enriched.update(
                    {
                        "final_url": document.final_url,
                        "accessed_at": document.accessed_at,
                        "content_sha256": document.content_sha256,
                        "cache_path": _relative_or_absolute(document.text_path, root),
                        "fetch_status": document.fetch_status,
                    }
                )
                enriched_sources.append(enriched)
            package["sources"] = enriched_sources
        except (ResearchValidationError, FetchError) as exc:
            if not can_retry or attempt == 2:
                raise
            provider.add_research_feedback(str(exc))
            continue

        excerpts = {
            source_id: text[:30000] for source_id, text in source_texts.items()
        }
        semantic_audit = provider.audit(package, excerpts)
        validate_semantic_audit(semantic_audit)
        audit = audit_research(
            package,
            source_texts,
            semantic_audit,
            coordinate_tolerance_km,
            minimum_sources,
        )
        if audit["passed"]:
            return package, audit
        gate_error = ResearchGateError("研究包未通过事实门禁", audit)
        if not can_retry or attempt == 2:
            raise gate_error
        provider.add_research_feedback("；".join(audit["critical_failures"][:8]))
    raise RuntimeError("研究重试循环意外结束")


def run_research(
    root: Path,
    destination: Path,
    provider,
    fetcher,
    topic: dict[str, Any] | None,
    research_date: str,
    coordinate_tolerance_km: float,
    minimum_sources: int,
    force: bool = False,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    try:
        package, audit = build_research_package(
            root,
            provider,
            fetcher,
            topic,
            research_date,
            coordinate_tolerance_km,
            minimum_sources,
        )
    except ResearchGateError as exc:
        topic_id = topic["topic_id"] if topic else "fixture"
        failure_path = (
            root
            / "output"
            / "_failed"
            / f"research-{research_date}-{topic_id}-{uuid.uuid4().hex[:8]}"
        )
        failure_path.mkdir(parents=True, exist_ok=False)
        (failure_path / "audit.json").write_text(
            json.dumps(exc.audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise ResearchGateError(
            f"{exc}；失败审计已保存到 {failure_path}",
            exc.audit,
            failure_path,
        ) from exc
    output = write_research_artifacts(destination, package, audit, force=force)
    return output, package, audit
