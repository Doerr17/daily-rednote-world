from __future__ import annotations

import unittest

import tests  # noqa: F401 - adds the src layout to sys.path for direct unittest runs

from worldnote.similarity import normalize_name, set_similarity, text_similarity


class SimilarityTests(unittest.TestCase):
    def test_alias_normalization_ignores_spacing_punctuation_and_accents(self):
        self.assertEqual(normalize_name("Aït-Ben-Haddou"), normalize_name("Ait Ben Haddou"))

    def test_identical_text_is_one(self):
        self.assertEqual(text_similarity("白色沙丘与潟湖", "白色沙丘与潟湖"), 1.0)

    def test_unrelated_text_is_low(self):
        self.assertLess(text_similarity("冰川进入峡湾", "彩色木屋矿城"), 0.2)

    def test_set_similarity(self):
        self.assertEqual(set_similarity(["岛屿", "冰川"], ["冰川", "矿业"]), 1 / 3)


if __name__ == "__main__":
    unittest.main()
