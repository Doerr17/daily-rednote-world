from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any


class ProviderError(RuntimeError):
    pass


class FixtureResearchProvider:
    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir

    def research(self, topic: dict[str, Any] | None, research_date: str) -> dict[str, Any]:
        package = json.loads(
            (self.fixture_dir / "fixture_draft.json").read_text(encoding="utf-8")
        )
        package["research_date"] = research_date
        return package

    def audit(self, package: dict[str, Any], excerpts: dict[str, str]) -> dict[str, Any]:
        return json.loads(
            (self.fixture_dir / "fixture_audit.json").read_text(encoding="utf-8")
        )


class FixtureWritingProvider:
    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir

    def write(self, package: dict[str, Any]) -> dict[str, Any]:
        return json.loads(
            (self.fixture_dir / "fixture_content.json").read_text(encoding="utf-8")
        )

    def audit(
        self, package: dict[str, Any], draft: dict[str, Any]
    ) -> dict[str, Any]:
        return json.loads(
            (self.fixture_dir / "fixture_audit.json").read_text(encoding="utf-8")
        )


class CodexResearchProvider:
    def __init__(self, root: Path, timeout_seconds: int = 900) -> None:
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.executable = shutil.which("codex.cmd") or shutil.which("codex")
        if not self.executable:
            raise ProviderError("未找到 codex.cmd/codex")
        self.research_feedback: list[str] = []

    def add_research_feedback(self, message: str) -> None:
        self.research_feedback.append(message)

    def build_command(self, schema: Path, output: Path, enable_search: bool) -> list[str]:
        command = [self.executable]
        if enable_search:
            command.append("--search")
        command.extend(
            [
                "exec",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "--cd",
                str(self.root),
                "-",
            ]
        )
        return command

    def _run(self, prompt: str, schema: Path, enable_search: bool) -> dict[str, Any]:
        temporary = self.root / "data" / "cache" / "model"
        temporary.mkdir(parents=True, exist_ok=True)
        prefix = "research" if enable_search else "audit"
        output = temporary / f"{prefix}-{uuid.uuid4().hex}.json"
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as stderr_capture:
            process = subprocess.Popen(
                self.build_command(schema, output, enable_search),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=stderr_capture,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                process.communicate(prompt, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                cleanup_note = ""
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=15,
                        )
                    except subprocess.TimeoutExpired:
                        cleanup_note = "；taskkill 清理命令也超时"
                else:
                    process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        cleanup_note = "；Codex 父进程未能在清理时限内退出"
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                raise ProviderError(
                    f"Codex 调用超时（{self.timeout_seconds} 秒）；已结束等待并执行进程清理{cleanup_note}"
                ) from exc
            stderr_capture.flush()
            stderr_capture.seek(0)
            stderr = stderr_capture.read()
        return_code = process.returncode
        if return_code != 0:
            detail = stderr.strip()[-2000:]
            raise ProviderError(f"Codex 调用失败（{return_code}）：{detail}")
        try:
            return json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderError("Codex 未生成可解析的结构化结果") from exc

    def research(self, topic: dict[str, Any] | None, research_date: str) -> dict[str, Any]:
        if not topic:
            raise ProviderError("Codex 研究需要真实候选选题")
        template = (self.root / "prompts" / "research.md").read_text(encoding="utf-8")
        topic_payload = dict(topic)
        topic_payload["research_date"] = research_date
        prompt = template.replace(
            "{topic_json}", json.dumps(topic_payload, ensure_ascii=False, indent=2)
        )
        feedback = ""
        if self.research_feedback:
            feedback = (
                "上一次尝试未通过本地硬门禁。重新研究并修正以下问题；"
                "不得复用其中不可访问的 URL：\n- "
                + "\n- ".join(self.research_feedback[-3:])
            )
        prompt = prompt.replace("{retry_feedback}", feedback)
        return self._run(
            prompt,
            self.root / "schemas" / "research_draft.schema.json",
            enable_search=True,
        )

    def audit(self, package: dict[str, Any], excerpts: dict[str, str]) -> dict[str, Any]:
        template = (self.root / "prompts" / "audit.md").read_text(encoding="utf-8")
        audit_input = {"research_package": package, "source_excerpts": excerpts}
        prompt = template.replace(
            "{audit_input_json}", json.dumps(audit_input, ensure_ascii=False, indent=2)
        )
        return self._run(
            prompt,
            self.root / "schemas" / "research_audit.schema.json",
            enable_search=False,
        )


class CachedResearchProvider(CodexResearchProvider):
    """Explicit recovery provider for a previously generated, human-reviewed draft.

    It never runs by default. The caller must opt in with a cache path; sources
    are fetched again and the ordinary research/audit gates still apply.
    """

    def __init__(self, root: Path, draft_path: Path, timeout_seconds: int = 900) -> None:
        super().__init__(root, timeout_seconds)
        self.draft_path = draft_path

    def research(self, topic: dict[str, Any] | None, research_date: str) -> dict[str, Any]:
        package = json.loads(self.draft_path.read_text(encoding="utf-8"))
        package["research_date"] = research_date
        if topic and package.get("topic_id") != topic.get("topic_id"):
            raise ProviderError("缓存研究稿与当天选题不一致")

        # Remove sources that were not reproducibly retrievable and claims that
        # relied only on them. This is intentionally narrow and auditable.
        allowed_sources = {"s1", "s3", "s4", "s5"}
        package["sources"] = [
            source for source in package["sources"]
            if source["source_id"] in allowed_sources
        ]
        claims = []
        for claim in package["claims"]:
            if claim["claim_id"] == "c7":
                continue
            refs = [ref for ref in claim["source_refs"] if ref["source_id"] in allowed_sources]
            if not refs:
                continue
            item = dict(claim)
            item["source_refs"] = refs
            claims.append(item)
        package["claims"] = claims
        claim_ids = {claim["claim_id"] for claim in claims}
        package["interesting_fact_claim_ids"] = [
            claim_id for claim_id in ("c1", "c3", "c5") if claim_id in claim_ids
        ]
        package["uniqueness_claim_ids"] = [
            claim_id for claim_id in ("c1", "c3", "c5") if claim_id in claim_ids
        ]
        package["travel_claim_ids"] = [
            claim_id for claim_id in package.get("travel_claim_ids", [])
            if claim_id in claim_ids
        ]
        for evidence in package["location"]["coordinate_evidence"]:
            if evidence["source_id"] == "s3":
                evidence["evidence_quote"] = '12°1\'54"N 39°2\'28.2"E'
        package["unknowns"] = list(package.get("unknowns", [])) + [
            "本次恢复稿未使用无法稳定抓取的 Met 来源及美国国务院页面；相关主张不进入成稿。"
        ]
        return package


class CodexWritingProvider(CodexResearchProvider):
    def __init__(self, root: Path, timeout_seconds: int = 900) -> None:
        super().__init__(root, timeout_seconds)
        self.content_feedback: list[str] = []

    def add_content_feedback(self, message: str) -> None:
        self.content_feedback.append(message)

    def write(self, package: dict[str, Any]) -> dict[str, Any]:
        template = (self.root / "prompts" / "writing.md").read_text(encoding="utf-8")
        prompt = template.replace(
            "{research_package_json}",
            json.dumps(package, ensure_ascii=False, indent=2),
        )
        feedback = ""
        if self.content_feedback:
            feedback = (
                "上一次文案未通过内容门禁。请逐项修正以下问题，保留事实引用，"
                "不要新增未在研究包中出现的内容：\n- "
                + "\n- ".join(self.content_feedback[-3:])
            )
        prompt = prompt.replace("{retry_feedback}", feedback)
        return self._run(
            prompt,
            self.root / "schemas" / "content_draft.schema.json",
            enable_search=False,
        )

    def audit(
        self, package: dict[str, Any], draft: dict[str, Any]
    ) -> dict[str, Any]:
        template = (self.root / "prompts" / "content_audit.md").read_text(
            encoding="utf-8"
        )
        prompt = template.replace(
            "{audit_input_json}",
            json.dumps(
                {"research_package": package, "content_draft": draft},
                ensure_ascii=False,
                indent=2,
            ),
        )
        return self._run(
            prompt,
            self.root / "schemas" / "content_audit.schema.json",
            enable_search=False,
        )
