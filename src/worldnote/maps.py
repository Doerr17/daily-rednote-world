from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import shapefile


class MapDataError(RuntimeError):
    pass


def ensure_map_data(root: Path, settings: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    directory = root / settings["directory"]
    archive = directory / "ne_110m_admin_0_countries.zip"
    manifest_path = directory / "source.json"
    shapefile_path = directory / "ne_110m_admin_0_countries.shp"
    if archive.is_file() and manifest_path.is_file() and shapefile_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if manifest.get("sha256") != digest:
            raise MapDataError("Natural Earth 缓存哈希不一致")
        return shapefile_path, manifest

    directory.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        settings["url"], headers={"User-Agent": "daily-world-rednote/0.1 map-data"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    handle, temporary_name = tempfile.mkstemp(prefix=".natural-earth-", dir=directory)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(data)
        with zipfile.ZipFile(temporary) as zipped:
            wanted = {
                "ne_110m_admin_0_countries.shp",
                "ne_110m_admin_0_countries.shx",
                "ne_110m_admin_0_countries.dbf",
                "ne_110m_admin_0_countries.prj",
                "ne_110m_admin_0_countries.cpg",
                "README.md",
                "VERSION.txt",
            }
            for member in zipped.infolist():
                name = Path(member.filename).name
                if name not in wanted:
                    continue
                target = directory / name
                target.write_bytes(zipped.read(member))
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    if not shapefile_path.is_file():
        raise MapDataError("Natural Earth 压缩包缺少国家边界 Shapefile")
    manifest = {
        "dataset": settings["dataset"],
        "version": settings["version"],
        "scale": settings["scale"],
        "source_url": settings["url"],
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license": "Public domain",
        "credit": "Made with Natural Earth",
        "boundary_note": "Natural Earth default de facto boundary view",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return shapefile_path, manifest


def _project(longitude: float, latitude: float, width: int, height: int) -> tuple[float, float]:
    return (longitude + 180.0) / 360.0 * width, (90.0 - latitude) / 180.0 * height


def _rings(shape: shapefile.Shape) -> list[list[tuple[float, float]]]:
    stops = list(shape.parts) + [len(shape.points)]
    return [shape.points[stops[index] : stops[index + 1]] for index in range(len(stops) - 1)]


def _contains(ring: list[tuple[float, float]], longitude: float, latitude: float) -> bool:
    inside = False
    previous = len(ring) - 1
    for current, (x_current, y_current) in enumerate(ring):
        x_previous, y_previous = ring[previous]
        crosses = (y_current > latitude) != (y_previous > latitude)
        if crosses:
            x_cross = (x_previous - x_current) * (latitude - y_current) / (
                y_previous - y_current
            ) + x_current
            if longitude < x_cross:
                inside = not inside
        previous = current
    return inside


def _path_for_ring(
    ring: list[tuple[float, float]], width: int, height: int
) -> list[str]:
    paths: list[str] = []
    current: list[str] = []
    previous_x: float | None = None
    for longitude, latitude in ring:
        x, y = _project(longitude, latitude, width, height)
        if previous_x is not None and abs(x - previous_x) > width / 2:
            if len(current) > 2:
                paths.append(" ".join(current))
            current = []
        current.append(f"{'M' if not current else 'L'} {x:.2f} {y:.2f}")
        previous_x = x
    if len(current) > 2:
        current.append("Z")
        paths.append(" ".join(current))
    return paths


def generate_locator_map(
    shapefile_path: Path,
    source_manifest: dict[str, Any],
    destination: Path,
    metadata_path: Path,
    location: dict[str, Any],
    accent: str = "#d96b43",
    width: int = 960,
    height: int = 520,
) -> dict[str, Any]:
    if location.get("coordinate_status") != "verified":
        raise MapDataError("只有 verified 坐标可以生成地图")
    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    reader = shapefile.Reader(str(shapefile_path))
    try:
        shapes = reader.shapes()
    finally:
        reader.close()
    containing: set[int] = set()
    for index, shape in enumerate(shapes):
        if any(_contains(ring, longitude, latitude) for ring in _rings(shape)):
            containing.add(index)
    paths: list[str] = []
    for index, shape in enumerate(shapes):
        fill = accent if index in containing else "#dfe6e4"
        for ring in _rings(shape):
            for path in _path_for_ring(ring, width, height):
                paths.append(
                    f'<path d="{path}" fill="{fill}" stroke="#ffffff" stroke-width="0.7"/>'
                )
    x, y = _project(longitude, latitude, width, height)
    place = html_escape(location["name_zh"])
    country = html_escape(location["country_zh"])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" data-latitude="{latitude}" data-longitude="{longitude}" data-projection="equirectangular">
<rect width="100%" height="100%" rx="28" fill="#f5f7f4"/>
<g>{''.join(paths)}</g>
<circle cx="{x:.2f}" cy="{y:.2f}" r="17" fill="none" stroke="#ffffff" stroke-width="7"/>
<circle cx="{x:.2f}" cy="{y:.2f}" r="11" fill="{accent}"/>
<text x="{min(x + 20, width - 250):.2f}" y="{max(y - 18, 34):.2f}" font-family="Microsoft YaHei, sans-serif" font-size="24" font-weight="700" fill="#1d2d32">{place}</text>
<text x="{min(x + 20, width - 250):.2f}" y="{max(y + 12, 64):.2f}" font-family="Microsoft YaHei, sans-serif" font-size="17" fill="#52646a">{country} · {latitude:.4f}, {longitude:.4f}</text>
</svg>"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(svg, encoding="utf-8")
    metadata = {
        "dataset": source_manifest["dataset"],
        "version": source_manifest["version"],
        "scale": source_manifest["scale"],
        "source_url": source_manifest["source_url"],
        "source_sha256": source_manifest["sha256"],
        "license": source_manifest["license"],
        "credit": source_manifest["credit"],
        "boundary_note": source_manifest["boundary_note"],
        "projection": "equirectangular",
        "latitude": latitude,
        "longitude": longitude,
        "point_pixel": {"x": round(x, 2), "y": round(y, 2)},
        "country_polygon_matches": len(containing),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
