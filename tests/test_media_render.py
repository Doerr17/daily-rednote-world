from __future__ import annotations

import json
import tempfile
import unittest
import shutil
from pathlib import Path

import tests  # noqa: F401
from PIL import Image

from tests.helpers import ROOT
from worldnote.config import load_config
from worldnote.maps import MapDataError, ensure_map_data, generate_locator_map
from worldnote.media import CommonsClient, MediaError, create_fixture_media, download_approved_candidate
from worldnote.render import (
    RenderError,
    _photo_assets_by_card_number,
    render_cards,
    validate_media_manifest,
)


CONFIG = load_config(ROOT)
MEDIA_FIXTURE = ROOT / "tests" / "fixtures" / "media" / "commons_response.json"


class MediaAndRenderTests(unittest.TestCase):
    def test_photo_mapping_skips_location_card(self):
        cards = [
            {"number": number, "role": role}
            for number, role in enumerate(
                ["cover", "location", "feature", "story", "facts", "summary"],
                start=1,
            )
        ]
        assets = [{"asset_id": f"photo-{index}"} for index in range(5)]
        mapped = _photo_assets_by_card_number(cards, assets)
        self.assertEqual(
            [mapped[number]["asset_id"] for number in (1, 3, 4, 5, 6)],
            [f"photo-{index}" for index in range(5)],
        )
        self.assertNotIn(2, mapped)

    def test_commons_metadata_requires_rights_and_location_evidence(self):
        payload = MEDIA_FIXTURE.read_bytes()
        client = CommonsClient(CONFIG.media, transport=lambda _: payload)
        candidates = client.search(
            "fixture", ["Fixture Rock-Hewn Place"], 12.0311, 39.0411
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["rights_status"], "complete")
        self.assertEqual(candidates[0]["location_match_status"], "verified")
        self.assertEqual(candidates[1]["rights_status"], "incomplete")
        self.assertEqual(candidates[1]["location_match_status"], "pending_review")

    def test_unverified_candidate_cannot_be_downloaded(self):
        candidate = {
            "rights_status": "complete",
            "location_match_status": "pending_review",
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MediaError, "地点一致性"):
                download_approved_candidate(
                    candidate, Path(directory) / "image.jpg", CONFIG.media
                )

    def test_map_uses_verified_coordinates_and_records_projection(self):
        shape_path, source = ensure_map_data(ROOT, CONFIG.map)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            location = {
                "name_zh": "测试点",
                "country_zh": "测试国",
                "latitude": 12.0311,
                "longitude": 39.0411,
                "coordinate_status": "verified",
            }
            metadata = generate_locator_map(
                shape_path, source, output / "map.svg", output / "map.json", location
            )
            svg = (output / "map.svg").read_text(encoding="utf-8")
            self.assertIn('data-latitude="12.0311"', svg)
            self.assertEqual(metadata["projection"], "equirectangular")
            self.assertEqual(metadata["source_sha256"], source["sha256"])
            location["coordinate_status"] = "pending_research"
            with self.assertRaisesRegex(MapDataError, "verified"):
                generate_locator_map(
                    shape_path, source, output / "bad.svg", output / "bad.json", location
                )

    def test_media_hash_or_missing_license_blocks_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = create_fixture_media(root, "fixture-rock-hewn-place")
            manifest["fixture_mode"] = False
            manifest["assets"][0]["license_url"] = ""
            with self.assertRaisesRegex(RenderError, "授权字段"):
                validate_media_manifest(manifest, manifest["topic_id"], 1080, 1080, 3)
            manifest["fixture_mode"] = True
            manifest["assets"][0]["sha256"] = "bad"
            with self.assertRaisesRegex(RenderError, "哈希"):
                validate_media_manifest(manifest, manifest["topic_id"], 1080, 1080, 3)

    def test_fixture_render_exports_exact_pngs_without_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest = create_fixture_media(
                temporary / "media", "fixture-rock-hewn-place"
            )
            output, audit = render_cards(
                ROOT,
                ROOT / "output" / "_fixtures" / "research",
                ROOT / "output" / "_fixtures" / "content",
                manifest,
                temporary / "rendered",
                CONFIG.map,
                CONFIG.render,
                CONFIG.media,
            )
            self.assertTrue(audit["passed"])
            self.assertEqual(len(audit["image_checks"]), 5)
            self.assertTrue(all(not value for value in audit["overflow_checks"].values()))
            for image_path in (output / "images").glob("*.png"):
                with Image.open(image_path) as image:
                    self.assertEqual(image.size, (1080, 1440))
            self.assertTrue((output / "assets" / "map_metadata.json").is_file())
            self.assertTrue((output / "image_sources.md").is_file())
            self.assertTrue((output / "review_checklist.md").is_file())
            rendered_manifest = json.loads(
                (output / "assets" / "media_manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                all(Path(asset["local_path"]).is_file() for asset in rendered_manifest["assets"])
            )
            checklist = (output / "review_checklist.md").read_text(encoding="utf-8")
            self.assertIn("全景/中远景", checklist)
            self.assertIn("主体足够大且清楚", checklist)
            self.assertTrue((output / "manifest.json").is_file())

    def test_tampered_content_is_rejected_before_browser_render(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            research = temporary / "research"
            content = temporary / "content"
            shutil.copytree(ROOT / "output" / "_fixtures" / "research", research)
            shutil.copytree(ROOT / "output" / "_fixtures" / "content", content)
            with (content / "content_package.json").open("a", encoding="utf-8") as handle:
                handle.write(" ")
            manifest = create_fixture_media(
                temporary / "media", "fixture-rock-hewn-place"
            )
            with self.assertRaisesRegex(RenderError, "内容包哈希"):
                render_cards(
                    ROOT,
                    research,
                    content,
                    manifest,
                    temporary / "rendered",
                    CONFIG.map,
                    CONFIG.render,
                    CONFIG.media,
                )


if __name__ == "__main__":
    unittest.main()
