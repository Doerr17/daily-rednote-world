from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from .research_validation import haversine_km
from .similarity import normalize_name


class MediaError(RuntimeError):
    pass


def _plain(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return " ".join(value.split())


def _metadata(info: dict[str, Any], name: str) -> str:
    return _plain(info.get("extmetadata", {}).get(name, {}).get("value", ""))


class CommonsClient:
    def __init__(
        self,
        settings: dict[str, Any],
        transport: Callable[[str], bytes] | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport or self._http_get

    def _http_get(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": str(self.settings["user_agent"])},
        )
        with urllib.request.urlopen(
            request, timeout=float(self.settings["request_timeout_seconds"])
        ) as response:
            return response.read()

    def _url(self, query: str) -> str:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",
            "gsrlimit": str(self.settings["search_limit"]),
            "prop": "imageinfo|coordinates|categories",
            "iiprop": "url|size|sha1|mime|extmetadata",
            "iiurlwidth": str(self.settings["thumbnail_width"]),
            "iiextmetadatalanguage": "en",
            "cllimit": "max",
            "colimit": "max",
            "origin": "*",
        }
        return f"{self.settings['commons_api']}?{urllib.parse.urlencode(params)}"

    def search(
        self,
        query: str,
        aliases: list[str],
        latitude: float,
        longitude: float,
    ) -> list[dict[str, Any]]:
        payload = json.loads(self.transport(self._url(query)).decode("utf-8"))
        pages = payload.get("query", {}).get("pages", [])
        accessed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return [
            self._candidate(page, aliases, latitude, longitude, accessed_at)
            for page in pages
            if page.get("imageinfo")
        ]

    def _candidate(
        self,
        page: dict[str, Any],
        aliases: list[str],
        latitude: float,
        longitude: float,
        accessed_at: str,
    ) -> dict[str, Any]:
        info = page["imageinfo"][0]
        creator = _metadata(info, "Artist")
        license_name = _metadata(info, "LicenseShortName") or _metadata(info, "UsageTerms")
        license_url = _metadata(info, "LicenseUrl")
        credit = _metadata(info, "Credit") or creator
        description = _metadata(info, "ImageDescription") or _metadata(info, "ObjectName")
        attribution_text = _metadata(info, "AttributionRequired").casefold()
        attribution_required = attribution_text not in {"false", "0", "no"}
        accepted = any(
            license_name.casefold().startswith(item.casefold())
            for item in self.settings["accepted_licenses"]
        )
        required_rights = all(
            [creator, license_name, info.get("descriptionurl"), info.get("url")]
        ) and (bool(license_url) or license_name.casefold() in {"public domain", "pdm", "cc0"})
        rights_status = "complete" if accepted and required_rights else (
            "unsupported" if license_name and not accepted else "incomplete"
        )

        categories = [item.get("title", "") for item in page.get("categories", [])]
        coordinate = None
        if page.get("coordinates"):
            first = page["coordinates"][0]
            coordinate = {"latitude": float(first["lat"]), "longitude": float(first["lon"])}
        normalized_aliases = [normalize_name(item) for item in aliases if item]
        name_fields = {
            "description": description,
            "categories": " ".join(categories),
        }
        evidence: list[str] = []
        matched_fields: set[str] = set()
        for field, value in name_fields.items():
            normalized = normalize_name(value)
            matched = next((alias for alias in normalized_aliases if alias and alias in normalized), None)
            if matched:
                evidence.append(f"{field} contains a normalized location alias")
                matched_fields.add(field)
        if coordinate:
            distance = haversine_km(
                latitude,
                longitude,
                coordinate["latitude"],
                coordinate["longitude"],
            )
            if distance <= float(self.settings["location_tolerance_km"]):
                evidence.append(
                    f"embedded coordinates are {distance:.2f} km from the audited location"
                )
            else:
                evidence.append(
                    f"embedded coordinates are {distance:.2f} km from the audited location"
                )
        has_name = bool(matched_fields)
        coordinate_close = bool(coordinate) and evidence[-1].startswith("embedded coordinates are") and (
            haversine_km(
                latitude,
                longitude,
                coordinate["latitude"],
                coordinate["longitude"],
            ) <= float(self.settings["location_tolerance_km"])
        )
        text_cross_check = {"description", "categories"}.issubset(matched_fields)
        location_status = (
            "verified" if text_cross_check or (has_name and coordinate_close) else "pending_review"
        )
        return {
            "title": page.get("title", ""),
            "source_page": info.get("descriptionurl", ""),
            "file_url": info.get("url", ""),
            "thumbnail_url": info.get("thumburl") or info.get("url", ""),
            "creator": creator,
            "license": license_name,
            "license_url": license_url,
            "credit": credit,
            "attribution_required": attribution_required,
            "width": int(info.get("width", 0)),
            "height": int(info.get("height", 0)),
            "mime": info.get("mime", ""),
            "commons_sha1": info.get("sha1", ""),
            "description": description,
            "categories": categories,
            "coordinates": coordinate,
            "rights_status": rights_status,
            "location_match_status": location_status,
            "location_match_evidence": evidence,
            "accessed_at": accessed_at,
        }


def download_approved_candidate(
    candidate: dict[str, Any],
    destination: Path,
    settings: dict[str, Any],
) -> dict[str, Any]:
    if candidate.get("rights_status") != "complete":
        raise MediaError("图片授权字段不完整或许可证不在允许范围")
    if candidate.get("location_match_status") != "verified":
        raise MediaError("图片地点一致性尚未核验")
    request = urllib.request.Request(
        candidate["thumbnail_url"],
        headers={"User-Agent": str(settings["user_agent"])},
    )
    with urllib.request.urlopen(
        request, timeout=float(settings["request_timeout_seconds"])
    ) as response:
        data = response.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(data)
        with Image.open(temporary) as image:
            image.verify()
        with Image.open(temporary) as image:
            width, height = image.size
        if width < int(settings["minimum_width"]) or height < int(settings["minimum_height"]):
            raise MediaError(f"图片分辨率不足：{width}x{height}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    result = dict(candidate)
    result.update(
        {
            "asset_id": destination.stem,
            "local_path": str(destination.resolve()),
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
            "crop_policy": "CSS object-fit cover; source file preserved",
        }
    )
    return result


def create_fixture_media(directory: Path, topic_id: str) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fixture-landscape.png"
    image = Image.new("RGB", (1800, 2400), "#20333b")
    draw = ImageDraw.Draw(image)
    for index in range(16):
        color = (45 + index * 5, 90 + index * 4, 100 + index * 3)
        draw.rectangle((0, index * 150, 1800, (index + 1) * 150), fill=color)
    draw.ellipse((480, 580, 1320, 1420), fill="#d49a63")
    draw.rectangle((280, 1540, 1520, 2050), fill="#15282f")
    image.save(path, format="PNG")
    data = path.read_bytes()
    return {
        "schema_version": "1.0",
        "topic_id": topic_id,
        "fixture_mode": True,
        "assets": [
            {
                "asset_id": "fixture-landscape",
                "local_path": str(path.resolve()),
                "source_page": "fixture://media/landscape",
                "file_url": "fixture://media/landscape.png",
                "creator": "Fixture generator",
                "license": "TEST FIXTURE ONLY",
                "license_url": "fixture://license",
                "credit": "TEST FIXTURE ONLY",
                "attribution_required": True,
                "accessed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "location_match_status": "verified",
                "location_match_evidence": ["synthetic fixture tied to the fixture topic"],
                "sha256": hashlib.sha256(data).hexdigest(),
                "width": 1800,
                "height": 2400,
                "crop_policy": "CSS object-fit cover; synthetic fixture",
            }
        ],
    }
