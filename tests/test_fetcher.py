from __future__ import annotations

import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import tests  # noqa: F401

from worldnote.fetcher import HttpFetcher, RobotsDenied, extract_text


class _Handler(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}
    disallow_blocked = False

    def do_GET(self):
        self.hits[self.path] = self.hits.get(self.path, 0) + 1
        if self.path == "/robots.txt":
            rule = "Disallow: /blocked" if self.disallow_blocked else "Allow: /"
            body = f"User-agent: *\n{rule}\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"<html><body><h1>Verified heading</h1><p>Evidence paragraph.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class FetcherTests(unittest.TestCase):
    def setUp(self):
        _Handler.hits = {}
        _Handler.disallow_blocked = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def fetcher(self):
        return HttpFetcher(
            Path(self.temp.name),
            "worldnote-test/1.0",
            timeout_seconds=2,
            retries=0,
            minimum_host_interval_seconds=0,
        )

    def test_fetch_extracts_text_hashes_and_uses_cache(self):
        fetcher = self.fetcher()
        first = fetcher.fetch(self.base + "/page")
        second = fetcher.fetch(self.base + "/page")
        self.assertIn("Evidence paragraph.", first.text)
        self.assertEqual(len(first.content_sha256), 64)
        self.assertEqual(second.fetch_status, "cached")
        self.assertEqual(_Handler.hits["/page"], 1)

    def test_robots_denial_stops_fetch(self):
        _Handler.disallow_blocked = True
        with self.assertRaises(RobotsDenied):
            self.fetcher().fetch(self.base + "/blocked")
        self.assertNotIn("/blocked", _Handler.hits)

    def test_corrupted_cached_body_is_refetched(self):
        fetcher = self.fetcher()
        first = fetcher.fetch(self.base + "/page")
        first.body_path.write_bytes(b"tampered")
        second = fetcher.fetch(self.base + "/page")
        self.assertEqual(second.fetch_status, "fetched")
        self.assertEqual(_Handler.hits["/page"], 2)

    def test_pdf_text_is_extracted_for_evidence_checks(self):
        page = MagicMock()
        page.extract_text.return_value = "Coordinates: 12.03, 39.04"
        reader = MagicMock()
        reader.pages = [page]
        with patch("worldnote.fetcher.PdfReader", return_value=reader):
            text = extract_text(b"%PDF-1.7\nfixture", "application/pdf")
        self.assertEqual(text, "Coordinates: 12.03, 39.04")


if __name__ == "__main__":
    unittest.main()
