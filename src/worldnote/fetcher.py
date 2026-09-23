from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from pypdf import PdfReader


class FetchError(RuntimeError):
    pass


class RobotsDenied(FetchError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        elif not self._skip_depth and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "br", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        elif not self._skip_depth and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


def extract_text(body: bytes, content_type: str) -> str:
    if "application/pdf" in content_type.lower() or body.startswith(b"%PDF-"):
        reader = PdfReader(io.BytesIO(body))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if not text:
            raise FetchError("PDF 不含可检索文本，不能用于证据核查")
        return text
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    decoded = body.decode(encoding, errors="replace")
    if "html" not in content_type.lower() and "<html" not in decoded[:500].lower():
        return decoded
    parser = _TextExtractor()
    parser.feed(decoded)
    return parser.text()


@dataclass(frozen=True)
class FetchedDocument:
    url: str
    final_url: str
    status_code: int
    content_type: str
    accessed_at: str
    content_sha256: str
    body_path: Path
    text_path: Path
    fetch_status: str
    text: str


class HttpFetcher:
    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        timeout_seconds: float = 25,
        retries: int = 2,
        minimum_host_interval_seconds: float = 1.0,
        cache_max_age_seconds: int = 86400,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.minimum_host_interval_seconds = minimum_host_interval_seconds
        self.cache_max_age_seconds = cache_max_age_seconds
        self.sleep = sleep
        self._host_last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _paths(self, url: str) -> tuple[Path, Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return (
            self.cache_dir / f"{key}.body",
            self.cache_dir / f"{key}.txt",
            self.cache_dir / f"{key}.json",
        )

    def _cached(self, url: str) -> FetchedDocument | None:
        body_path, text_path, metadata_path = self._paths(url)
        if not (body_path.is_file() and text_path.is_file() and metadata_path.is_file()):
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(metadata["accessed_at"])
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age > self.cache_max_age_seconds:
            return None
        body = body_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != metadata["content_sha256"]:
            return None
        text = extract_text(body, metadata["content_type"])
        if text_path.read_text(encoding="utf-8") != text:
            text_path.write_text(text, encoding="utf-8")
        return FetchedDocument(
            url=url,
            final_url=metadata["final_url"],
            status_code=int(metadata["status_code"]),
            content_type=metadata["content_type"],
            accessed_at=metadata["accessed_at"],
            content_sha256=metadata["content_sha256"],
            body_path=body_path,
            text_path=text_path,
            fetch_status="cached",
            text=text,
        )

    def _wait_for_host(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc.casefold()
        last = self._host_last_request.get(host)
        if last is not None:
            wait = self.minimum_host_interval_seconds - (time.monotonic() - last)
            if wait > 0:
                self.sleep(wait)
        self._host_last_request[host] = time.monotonic()

    def _open(self, url: str) -> tuple[bytes, str, int, str]:
        self._wait_for_host(url)
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return (
                response.read(),
                response.geturl(),
                int(response.status),
                response.headers.get("Content-Type", "application/octet-stream"),
            )

    def _robots_allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            robots_url = urllib.parse.urljoin(origin, "/robots.txt")
            try:
                body, _, status, _ = self._open(robots_url)
                if status == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.set_url(robots_url)
                    parser.parse(body.decode("utf-8", errors="replace").splitlines())
                    self._robots[origin] = parser
                else:
                    self._robots[origin] = None
            except (OSError, urllib.error.URLError, urllib.error.HTTPError):
                self._robots[origin] = None
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    def fetch(self, url: str) -> FetchedDocument:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise FetchError(f"只允许抓取 http/https URL：{url}")
        cached = self._cached(url)
        if cached:
            return cached
        if not self._robots_allowed(url):
            raise RobotsDenied(f"robots.txt 不允许抓取：{url}")

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                body, final_url, status, content_type = self._open(url)
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in self.RETRYABLE or attempt >= self.retries:
                    raise FetchError(f"HTTP {exc.code}: {url}") from exc
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise FetchError(f"抓取失败：{url}: {exc}") from exc
            self.sleep(min(2**attempt, 4))
        else:
            raise FetchError(f"抓取失败：{url}: {last_error}")

        text = extract_text(body, content_type)
        digest = hashlib.sha256(body).hexdigest()
        accessed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        body_path, text_path, metadata_path = self._paths(url)
        body_path.write_bytes(body)
        text_path.write_text(text, encoding="utf-8")
        metadata_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "final_url": final_url,
                    "status_code": status,
                    "content_type": content_type,
                    "accessed_at": accessed_at,
                    "content_sha256": digest,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return FetchedDocument(
            url=url,
            final_url=final_url,
            status_code=status,
            content_type=content_type,
            accessed_at=accessed_at,
            content_sha256=digest,
            body_path=body_path,
            text_path=text_path,
            fetch_status="fetched",
            text=text,
        )


class FixtureFetcher:
    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        self.mapping = {
            "fixture://research/source-one": fixture_dir / "source-one.txt",
            "fixture://research/source-two": fixture_dir / "source-two.txt",
        }

    def fetch(self, url: str) -> FetchedDocument:
        path = self.mapping.get(url)
        if path is None or not path.is_file():
            raise FetchError(f"未知 fixture URL：{url}")
        body = path.read_bytes()
        text = body.decode("utf-8")
        return FetchedDocument(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/plain; charset=utf-8",
            accessed_at="2026-09-21T00:00:00+00:00",
            content_sha256=hashlib.sha256(body).hexdigest(),
            body_path=path,
            text_path=path,
            fetch_status="fixture",
            text=text,
        )
