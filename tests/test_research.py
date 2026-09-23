from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401

from tests.helpers import ROOT, seeded_database
from worldnote.fetcher import FixtureFetcher
from worldnote.providers import CodexResearchProvider, FixtureResearchProvider
from worldnote.research import ResearchGateError, build_research_package, run_research
from worldnote.research_audit import audit_research
from worldnote.research_store import persist_research
from worldnote.research_validation import validate_draft, validate_schema_documents


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "research"


class ResearchTests(unittest.TestCase):
    def fixture_package(self):
        provider = FixtureResearchProvider(FIXTURE_DIR)
        package = provider.research(None, "2026-09-21")
        fetcher = FixtureFetcher(FIXTURE_DIR)
        texts = {}
        enriched = []
        for source in package["sources"]:
            doc = fetcher.fetch(source["url"])
            item = dict(source)
            item.update(
                final_url=doc.final_url,
                accessed_at=doc.accessed_at,
                content_sha256=doc.content_sha256,
                cache_path=str(doc.text_path),
                fetch_status=doc.fetch_status,
            )
            enriched.append(item)
            texts[source["source_id"]] = doc.text
        package["sources"] = enriched
        semantic = provider.audit(package, texts)
        return package, texts, semantic

    def test_schema_documents_are_valid_json(self):
        validate_schema_documents(ROOT / "schemas")

    def test_fixture_research_passes_and_writes_auditable_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output, package, audit = run_research(
                ROOT,
                Path(directory) / "result",
                FixtureResearchProvider(FIXTURE_DIR),
                FixtureFetcher(FIXTURE_DIR),
                None,
                "2026-09-21",
                10.0,
                2,
            )
            self.assertTrue(audit["passed"])
            self.assertTrue(package["fixture_mode"])
            self.assertTrue((output / "research.md").is_file())
            self.assertIn("TEST FIXTURE ONLY", (output / "research.md").read_text(encoding="utf-8"))
            self.assertTrue((output / "sources.md").is_file())
            self.assertTrue((output / "location.json").is_file())
            self.assertTrue((output / "audit.json").is_file())
            written_audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(
                written_audit["research_package_sha256"],
                hashlib.sha256((output / "research_package.json").read_bytes()).hexdigest(),
            )

    def test_missing_quote_fails_hard_gate(self):
        package, texts, semantic = self.fixture_package()
        package["claims"][0]["source_refs"][0]["evidence_quote"] = "This quote is absent."
        audit = audit_research(package, texts, semantic, 10.0, 2)
        self.assertFalse(audit["passed"])
        self.assertTrue(any("未在缓存正文" in item for item in audit["critical_failures"]))

    def test_structural_failure_is_retried_with_feedback(self):
        class RetryingProvider(FixtureResearchProvider):
            def __init__(self, fixture_dir):
                super().__init__(fixture_dir)
                self.calls = 0
                self.feedback = []

            def research(self, topic, research_date):
                self.calls += 1
                package = super().research(topic, research_date)
                if self.calls == 1:
                    package["interesting_fact_claim_ids"][0] = "C1 with explanation"
                return package

            def add_research_feedback(self, message):
                self.feedback.append(message)

        provider = RetryingProvider(FIXTURE_DIR)
        package, audit = build_research_package(
            ROOT, provider, FixtureFetcher(FIXTURE_DIR), None, "2026-09-21", 10.0, 2
        )
        self.assertTrue(audit["passed"])
        self.assertEqual(provider.calls, 2)
        self.assertTrue(provider.feedback)
        self.assertEqual(package["interesting_fact_claim_ids"][0], "c01")

    def test_coordinate_conflict_fails_hard_gate(self):
        package, texts, semantic = self.fixture_package()
        package["location"]["coordinate_evidence"][1]["latitude"] = 22.0
        audit = audit_research(package, texts, semantic, 10.0, 2)
        self.assertFalse(audit["passed"])
        self.assertTrue(any("坐标来源冲突" in item for item in audit["critical_failures"]))

    def test_coordinate_quote_must_exist_in_cached_source(self):
        package, texts, semantic = self.fixture_package()
        package["location"]["coordinate_evidence"][0]["evidence_quote"] = "Absent coordinate quote"
        audit = audit_research(package, texts, semantic, 10.0, 2)
        self.assertFalse(audit["passed"])
        self.assertTrue(any("坐标证据摘录" in item for item in audit["critical_failures"]))

    def test_coordinate_sources_must_have_distinct_publishers(self):
        package, texts, semantic = self.fixture_package()
        package["sources"][1]["publisher"] = package["sources"][0]["publisher"]
        audit = audit_research(package, texts, semantic, 10.0, 2)
        self.assertFalse(audit["passed"])
        self.assertTrue(any("独立发布者" in item for item in audit["critical_failures"]))

    def test_semantic_unsupported_fails_hard_gate(self):
        package, texts, semantic = self.fixture_package()
        semantic["claim_reviews"][0]["status"] = "unsupported"
        audit = audit_research(package, texts, semantic, 10.0, 2)
        self.assertFalse(audit["passed"])
        self.assertTrue(any("独立审计未通过" in item for item in audit["critical_failures"]))

    def test_failed_audit_is_preserved_outside_formal_output(self):
        class FailingProvider(FixtureResearchProvider):
            def audit(self, package, excerpts):
                result = super().audit(package, excerpts)
                result["claim_reviews"][0]["status"] = "unsupported"
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ResearchGateError) as caught:
                run_research(
                    root,
                    root / "formal",
                    FailingProvider(FIXTURE_DIR),
                    FixtureFetcher(FIXTURE_DIR),
                    None,
                    "2026-09-21",
                    10.0,
                    2,
                )
            self.assertFalse((root / "formal").exists())
            self.assertIsNotNone(caught.exception.failure_path)
            self.assertTrue((caught.exception.failure_path / "audit.json").is_file())

    def test_fixture_cannot_enter_formal_database(self):
        package, texts, semantic = self.fixture_package()
        audit = audit_research(package, texts, semantic, 10.0, 2)
        with tempfile.TemporaryDirectory() as directory:
            connection = seeded_database(Path(directory))
            topic_row_id = connection.execute("SELECT id FROM topic_candidates LIMIT 1").fetchone()[0]
            with self.assertRaisesRegex(ValueError, "fixture"):
                persist_research(connection, topic_row_id, "fixture", package, audit, "fixture")
            connection.close()

    def test_passed_nonfixture_package_is_persisted_atomically(self):
        package, texts, semantic = self.fixture_package()
        package["fixture_mode"] = False
        audit = audit_research(package, texts, semantic, 10.0, 2)
        with tempfile.TemporaryDirectory() as directory:
            connection = seeded_database(Path(directory))
            topic_row = connection.execute(
                "SELECT id, location_id FROM topic_candidates LIMIT 1"
            ).fetchone()
            run_id = persist_research(
                connection,
                topic_row["id"],
                "test",
                package,
                audit,
                "test-output",
            )
            self.assertTrue(run_id)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM research_sources").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM research_claims").fetchone()[0], 3
            )
            status = connection.execute(
                "SELECT coordinate_status FROM locations WHERE id=?",
                (topic_row["location_id"],),
            ).fetchone()[0]
            self.assertEqual(status, "verified")
            connection.close()

    def test_draft_rejects_time_sensitive_claim_without_date(self):
        draft = json.loads((FIXTURE_DIR / "fixture_draft.json").read_text(encoding="utf-8"))
        draft["claims"][0]["volatility"] = "time_sensitive"
        with self.assertRaisesRegex(ValueError, "as_of"):
            validate_draft(draft)

    def test_codex_command_is_read_only_ephemeral_and_search_is_explicit(self):
        provider = CodexResearchProvider(ROOT, 30)
        command = provider.build_command(
            ROOT / "schemas" / "research_draft.schema.json",
            ROOT / "data" / "cache" / "model" / "test.json",
            enable_search=True,
        )
        self.assertLess(command.index("--search"), command.index("exec"))
        self.assertIn("read-only", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--output-schema", command)
        self.assertIn("--skip-git-repo-check", command)


if __name__ == "__main__":
    unittest.main()
