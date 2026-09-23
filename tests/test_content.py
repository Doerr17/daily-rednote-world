from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401

from tests.helpers import ROOT, seeded_database
from worldnote.config import load_config
from worldnote.content import ContentGateError, build_content_package, run_content
from worldnote.content_store import persist_content, recent_content_texts
from worldnote.content_validation import ContentValidationError, audit_content, load_audited_research
from worldnote.fetcher import FixtureFetcher
from worldnote.knowledge import archive_knowledge, search_knowledge
from worldnote.providers import (
    CodexWritingProvider,
    FixtureResearchProvider,
    FixtureWritingProvider,
)
from worldnote.research import run_research
from worldnote.research_store import persist_research
from worldnote.selector import get_topic_by_id


RESEARCH_FIXTURES = ROOT / "tests" / "fixtures" / "research"
CONTENT_FIXTURES = ROOT / "tests" / "fixtures" / "content"
SETTINGS = load_config(ROOT).content


class ContentTests(unittest.TestCase):
    def make_research(self, parent: Path) -> Path:
        output, _, _ = run_research(
            parent,
            parent / "research",
            FixtureResearchProvider(RESEARCH_FIXTURES),
            FixtureFetcher(RESEARCH_FIXTURES),
            None,
            "2026-09-21",
            10.0,
            2,
        )
        return output

    def load_inputs(self, parent: Path):
        research = self.make_research(parent)
        package, _, _ = load_audited_research(research)
        draft = FixtureWritingProvider(CONTENT_FIXTURES).write(package)
        semantic = FixtureWritingProvider(CONTENT_FIXTURES).audit(package, draft)
        return research, package, draft, semantic

    def test_fixture_content_writes_complete_reviewable_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research = self.make_research(root)
            output, _, _, audit, _ = run_content(
                root,
                research,
                root / "content",
                FixtureWritingProvider(CONTENT_FIXTURES),
                SETTINGS,
            )
            self.assertTrue(audit["passed"])
            expected = {
                "content.md", "learning_notes.md", "knowledge_entry.md", "cards.json",
                "content_package.json", "content_audit.json",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            content_text = (output / "content.md").read_text(encoding="utf-8")
            self.assertIn("TEST FIXTURE ONLY", content_text)
            self.assertIn("## 标题候选", content_text)
            self.assertIn("## 发布正文", content_text)
            self.assertNotIn("证据：", content_text)
            self.assertNotIn("`c", content_text)

    def test_tampered_research_package_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research = self.make_research(root)
            with (research / "research_package.json").open("a", encoding="utf-8") as handle:
                handle.write(" ")
            with self.assertRaisesRegex(ContentValidationError, "哈希"):
                build_content_package(
                    research,
                    FixtureWritingProvider(CONTENT_FIXTURES),
                    SETTINGS,
                )

    def test_unknown_claim_reference_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            _, package, draft, semantic = self.load_inputs(Path(directory))
            draft["cards"][0]["blocks"][0]["claim_refs"] = ["c99"]
            audit = audit_content(package, draft, semantic, SETTINGS)
            self.assertFalse(audit["passed"])
            self.assertTrue(any("未知主张" in item for item in audit["critical_failures"]))

    def test_blocked_and_false_visit_phrases_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            _, package, draft, semantic = self.load_inputs(Path(directory))
            draft["caption"]["paragraphs"][0]["text"] = "你知道吗，我去过这个地点。"
            audit = audit_content(package, draft, semantic, SETTINGS)
            self.assertFalse(audit["passed"])
            self.assertTrue(any("禁用表达" in item for item in audit["critical_failures"]))
            self.assertTrue(any("虚假到访" in item for item in audit["critical_failures"]))

    def test_unsupported_number_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            _, package, draft, semantic = self.load_inputs(Path(directory))
            draft["cards"][2]["blocks"][0]["text"] = "这里共有 99 座建筑。"
            audit = audit_content(package, draft, semantic, SETTINGS)
            self.assertFalse(audit["passed"])
            self.assertTrue(any("未支持的数字" in item for item in audit["critical_failures"]))

    def test_wrong_card_order_and_internal_repetition_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            _, package, draft, semantic = self.load_inputs(Path(directory))
            draft["cards"][0]["role"] = "feature"
            draft["cards"][2]["heading"] = draft["cards"][3]["heading"]
            draft["cards"][2]["blocks"] = copy.deepcopy(draft["cards"][3]["blocks"])
            audit = audit_content(package, draft, semantic, SETTINGS)
            self.assertFalse(audit["passed"])
            self.assertTrue(any("前两张" in item for item in audit["critical_failures"]))
            self.assertTrue(any("重复度" in item for item in audit["critical_failures"]))

    def test_semantic_error_and_recent_duplicate_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            _, package, draft, semantic = self.load_inputs(Path(directory))
            semantic["issues"] = [{
                "severity": "error", "code": "scope_expansion",
                "location": "caption", "message": "超出研究包",
            }]
            recent = [{
                "fingerprint_type": "caption",
                "text_value": "\n".join(item["text"] for item in draft["caption"]["paragraphs"]),
            }]
            audit = audit_content(package, draft, semantic, SETTINGS, recent)
            self.assertFalse(audit["passed"])
            self.assertTrue(any("语义审计" in item for item in audit["critical_failures"]))
            self.assertTrue(any("近期内容" in item for item in audit["critical_failures"]))

    def test_fixture_cannot_persist_or_pollute_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = seeded_database(root)
            research, package, draft, semantic = self.load_inputs(root / "files")
            audit = audit_content(package, draft, semantic, SETTINGS)
            topic = get_topic_by_id(connection, "lalibela-hewn-from-rock")
            with self.assertRaisesRegex(ValueError, "fixture"):
                persist_content(connection, topic["id"], "fixture", package, draft, audit, "x", "x")
            with self.assertRaisesRegex(ValueError, "fixture"):
                archive_knowledge(root, topic, package, draft, root / "output")
            self.assertFalse((root / "knowledge").exists())
            connection.close()

    def test_nonfixture_content_persists_and_knowledge_is_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = seeded_database(root)
            _, package, draft, semantic = self.load_inputs(root / "files")
            topic = get_topic_by_id(connection, "lalibela-hewn-from-rock")
            package["fixture_mode"] = False
            package["topic_id"] = topic["topic_id"]
            draft["fixture_mode"] = False
            draft["topic_id"] = topic["topic_id"]
            audit = audit_content(package, draft, semantic, SETTINGS)
            persist_research(connection, topic["id"], "test", package, {
                "passed": True,
                "semantic_audit": {
                    "claim_reviews": [
                        {"claim_id": claim["claim_id"], "status": "supported"}
                        for claim in package["claims"]
                    ]
                },
            }, "research-output")
            run_id = persist_content(
                connection, topic["id"], "test", package, draft, audit,
                "content-output", "abc",
            )
            self.assertTrue(run_id)
            self.assertEqual(len(recent_content_texts(connection, 5)), 4)
            entry = archive_knowledge(root, topic, package, draft, root / "output")
            self.assertTrue(entry.is_file())
            rows = search_knowledge(
                root,
                country=package["location"]["country_zh"],
                theme=topic["themes"][0],
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["slug"], topic["slug"])
            connection.close()

    def test_codex_writing_command_has_no_search_and_is_read_only(self):
        provider = CodexWritingProvider(ROOT, 30)
        command = provider.build_command(
            ROOT / "schemas" / "content_draft.schema.json",
            ROOT / "data" / "cache" / "model" / "content-test.json",
            enable_search=False,
        )
        self.assertNotIn("--search", command)
        self.assertIn("read-only", command)
        self.assertIn("--ephemeral", command)


if __name__ == "__main__":
    unittest.main()
