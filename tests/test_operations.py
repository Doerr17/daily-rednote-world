from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401

from tests.helpers import ROOT, seeded_database
from worldnote.config import load_config
from worldnote.daily import DailyError, run_daily, run_fixture_daily
from worldnote.db import utc_now
from worldnote.operations import add_metrics, list_metrics, publish_run
from worldnote.selector import choose_topic, record_selection


CONFIG = load_config(ROOT)


class OperationsTests(unittest.TestCase):
    def insert_daily_run(self, connection, status: str = "awaiting_review") -> None:
        topic_id = connection.execute("SELECT id FROM topic_candidates LIMIT 1").fetchone()[0]
        now = utc_now()
        connection.execute(
            """
            INSERT INTO daily_runs(
                run_id, run_date, topic_id, provider, status, workspace_dir,
                final_dir, error, created_at, updated_at
            ) VALUES ('run-1', '2026-09-22', ?, 'test', ?, 'work', 'final', NULL, ?, ?)
            """,
            (topic_id, status, now, now),
        )
        connection.commit()

    def test_publish_requires_explicit_confirmation_and_awaiting_review(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = seeded_database(Path(directory))
            self.insert_daily_run(connection)
            with self.assertRaisesRegex(ValueError, "confirm-reviewed"):
                publish_run(connection, "2026-09-22", utc_now(), None, False)
            publication_id = publish_run(
                connection, "2026-09-22", utc_now(), "https://example.invalid/post", True
            )
            self.assertGreater(publication_id, 0)
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM daily_runs WHERE run_date='2026-09-22'"
                ).fetchone()[0],
                "published",
            )
            with self.assertRaisesRegex(ValueError, "已登记发布"):
                publish_run(connection, "2026-09-22", utc_now(), None, True)
            connection.close()

    def test_metrics_require_publication_nonnegative_values_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = seeded_database(Path(directory))
            self.insert_daily_run(connection)
            with self.assertRaisesRegex(ValueError, "尚未人工登记发布"):
                add_metrics(connection, "2026-09-22", "2026-09-23", 1, 1, 1, 1, 1)
            publish_run(connection, "2026-09-22", utc_now(), None, True)
            with self.assertRaisesRegex(ValueError, "非负"):
                add_metrics(connection, "2026-09-22", "2026-09-23", -1, 1, 1, 1, 1)
            add_metrics(connection, "2026-09-22", "2026-09-23", 100, 8, 12, 2, 1)
            rows = list_metrics(connection, "2026-09-22")
            self.assertEqual(rows[0]["views"], 100)
            self.assertEqual(rows[0]["saves"], 12)
            with self.assertRaisesRegex(ValueError, "不会覆盖"):
                add_metrics(connection, "2026-09-22", "2026-09-23", 110, 9, 13, 3, 1)
            connection.close()

    def test_fixture_daily_resumes_after_injected_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "tests" / "fixtures", root / "tests" / "fixtures")
            shutil.copytree(ROOT / "templates", root / "templates")
            shutil.copytree(
                ROOT / "data" / "geographic",
                root / "data" / "geographic",
            )
            config = dataclasses.replace(CONFIG, root=root)
            with self.assertRaisesRegex(DailyError, "injected failure"):
                run_fixture_daily(root, config, "2026-09-22", fail_after="drafted")
            state_path = root / "output" / "_fixtures" / "daily" / "run_state.json"
            failed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(failed["status"], "failed")
            self.assertIn("drafted", failed["completed"])
            research_file = (
                root / "output" / "_fixtures" / "daily" / "workspace" / "research" / "research_package.json"
            )
            original_mtime = research_file.stat().st_mtime_ns
            final = run_fixture_daily(root, config, "2026-09-22")
            self.assertEqual(research_file.stat().st_mtime_ns, original_mtime)
            resumed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(resumed["status"], "awaiting_review")
            self.assertTrue((final / "images" / "01_cover.png").is_file())
            self.assertTrue((final / "manifest.json").is_file())
            final_content = (final / "content.md").read_text(encoding="utf-8")
            self.assertIn("## 标题候选", final_content)
            self.assertIn("## 发布正文", final_content)
            self.assertFalse((final / "title_options.md").exists())
            self.assertFalse((final / "caption.md").exists())
            self.assertFalse((root / "knowledge").exists())

    def test_fixture_restart_rebuilds_only_requested_downstream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "tests" / "fixtures", root / "tests" / "fixtures")
            shutil.copytree(ROOT / "templates", root / "templates")
            shutil.copytree(ROOT / "data" / "geographic", root / "data" / "geographic")
            config = dataclasses.replace(CONFIG, root=root)
            final = run_fixture_daily(root, config, "2026-09-22")
            research_file = root / "output" / "_fixtures" / "daily" / "workspace" / "research" / "research_package.json"
            research_mtime = research_file.stat().st_mtime_ns
            rebuilt = run_fixture_daily(
                root, config, "2026-09-22", restart_stage="rendered"
            )
            self.assertEqual(final, rebuilt)
            self.assertEqual(research_file.stat().st_mtime_ns, research_mtime)
            state = json.loads(
                (root / "output" / "_fixtures" / "daily" / "run_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "awaiting_review")

    def test_daily_recovers_selection_created_before_run_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connection = seeded_database(root)
            selection = choose_topic(
                connection,
                "2026-09-22",
                "recovery",
                CONFIG.recent_history_limit,
                CONFIG.hard_similarity_threshold,
                CONFIG.selection_weights,
            )
            record_selection(connection, "2026-09-22", selection)
            config = dataclasses.replace(CONFIG, root=root)
            with mock.patch(
                "worldnote.daily.run_research",
                side_effect=DailyError("stop after run record"),
            ):
                with self.assertRaisesRegex(DailyError, "stop after run record"):
                    run_daily(root, connection, config, "2026-09-22", "recovery")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM selections").fetchone()[0], 1
            )
            run = connection.execute(
                "SELECT topic_id, status FROM daily_runs WHERE run_date='2026-09-22'"
            ).fetchone()
            self.assertEqual(run["topic_id"], selection.topic["id"])
            self.assertEqual(run["status"], "failed")
            connection.close()


if __name__ == "__main__":
    unittest.main()
