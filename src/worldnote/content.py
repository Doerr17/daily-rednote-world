from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .content_artifacts import write_content_artifacts
from .content_validation import audit_content, load_audited_research


class ContentGateError(RuntimeError):
    def __init__(self, message: str, audit: dict[str, Any], failure_path: Path | None = None):
        super().__init__(message)
        self.audit = audit
        self.failure_path = failure_path


def build_content_package(
    research_directory: Path,
    provider,
    settings: dict[str, Any],
    recent_texts: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    package, research_audit, research_sha256 = load_audited_research(research_directory)
    can_retry = callable(getattr(provider, "add_content_feedback", None))
    for attempt in range(3):
        draft = provider.write(package)
        semantic_audit = provider.audit(package, draft)
        audit = audit_content(package, draft, semantic_audit, settings, recent_texts)
        audit["research_audit_version"] = research_audit.get("audit_version")
        if audit["passed"]:
            return package, draft, audit, research_sha256
        if not can_retry or attempt == 2:
            raise ContentGateError("文案未通过内容门禁", audit)
        feedback = audit.get("critical_failures", []) + audit.get("warnings", [])
        provider.add_content_feedback("；".join(feedback[:8]))
    raise RuntimeError("文案重试循环意外结束")


def run_content(
    root: Path,
    research_directory: Path,
    destination: Path,
    provider,
    settings: dict[str, Any],
    recent_texts: list[dict[str, str]] | None = None,
    force: bool = False,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], str]:
    try:
        package, draft, audit, research_sha256 = build_content_package(
            research_directory, provider, settings, recent_texts
        )
    except ContentGateError as exc:
        failure_path = root / "output" / "_failed" / f"content-{uuid.uuid4().hex[:8]}"
        failure_path.mkdir(parents=True, exist_ok=False)
        (failure_path / "content_audit.json").write_text(
            json.dumps(exc.audit, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise ContentGateError(
            f"{exc}；失败审计已保存到 {failure_path}", exc.audit, failure_path
        ) from exc
    output, content_sha256 = write_content_artifacts(
        destination, package, draft, audit, research_sha256, force=force
    )
    return output, package, draft, audit, content_sha256
