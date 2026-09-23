from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401 - adds the src layout to sys.path for direct unittest runs

from worldnote.config import load_config
from worldnote.db import utc_now
from worldnote.selector import SelectionError, choose_topic, record_selection

from tests.helpers import ROOT, seeded_database


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connection = seeded_database(Path(self.temp.name))
        self.config = load_config(ROOT)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def choose(self, day="2026-09-21", seed="fixed"):
        return choose_topic(
            self.connection,
            day,
            seed,
            self.config.recent_history_limit,
            self.config.hard_similarity_threshold,
            self.config.selection_weights,
        )

    def test_fixed_seed_and_date_are_reproducible(self):
        first = self.choose()
        second = self.choose()
        self.assertEqual(first.topic["topic_id"], second.topic["topic_id"])
        self.assertEqual(first.score, second.score)

    def test_used_location_is_excluded(self):
        first = self.choose()
        record_selection(self.connection, "2026-09-21", first)
        second = self.choose("2026-09-22")
        self.assertNotEqual(first.topic["slug"], second.topic["slug"])

    def test_same_country_and_type_receive_penalties(self):
        sewell_id = self.connection.execute(
            "SELECT id FROM topic_candidates WHERE topic_id='sewell-staircase-town'"
        ).fetchone()[0]
        self.connection.execute(
            "INSERT INTO selections(selected_on, topic_id, status, score, duplicate_score, "
            "score_details_json, created_at) VALUES (?, ?, 'selected', 0, 0, '{}', ?)",
            ("2026-09-20", sewell_id, utc_now()),
        )
        self.connection.commit()
        selection = self.choose("2026-09-21")
        self.assertNotEqual(selection.topic["topic_id"], "roros-wooden-mining-town")

    def test_all_remaining_topics_too_similar_stops_selection(self):
        first = self.choose()
        record_selection(self.connection, "2026-09-20", first)
        remaining_ids = [
            row[0]
            for row in self.connection.execute(
                "SELECT id FROM topic_candidates WHERE location_id != ?",
                (first.topic["location_id"],),
            )
        ]
        for topic_id in remaining_ids:
            self.connection.execute(
                "UPDATE topic_candidates SET hook_zh=?, angle_zh=? WHERE id=?",
                (first.topic["hook_zh"], first.topic["angle_zh"], topic_id),
            )
            self.connection.execute("DELETE FROM topic_tags WHERE topic_id=?", (topic_id,))
            for tag_type, values in (
                ("theme", first.topic["themes"]),
                ("visual", first.topic["visual_tags"]),
            ):
                for value in values:
                    self.connection.execute(
                        "INSERT INTO topic_tags(topic_id, tag_type, tag) VALUES (?, ?, ?)",
                        (topic_id, tag_type, value),
                    )
        self.connection.commit()
        with self.assertRaisesRegex(SelectionError, "过于相似"):
            self.choose("2026-09-21")

    def test_all_locations_used_stops_selection(self):
        topic_ids = [row[0] for row in self.connection.execute("SELECT id FROM topic_candidates")]
        for index, topic_id in enumerate(topic_ids, start=1):
            self.connection.execute(
                "INSERT INTO selections(selected_on, topic_id, status, score, duplicate_score, "
                "score_details_json, created_at) VALUES (?, ?, 'selected', 0, 0, ?, ?)",
                (f"2026-08-{index:02d}", topic_id, json.dumps({}), utc_now()),
            )
        self.connection.commit()
        with self.assertRaisesRegex(SelectionError, "没有未使用"):
            self.choose()

    def test_duplicate_day_is_not_overwritten(self):
        selection = self.choose()
        record_selection(self.connection, "2026-09-21", selection)
        with self.assertRaisesRegex(SelectionError, "不会自动覆盖"):
            record_selection(self.connection, "2026-09-21", self.choose("2026-09-22"))


if __name__ == "__main__":
    unittest.main()
