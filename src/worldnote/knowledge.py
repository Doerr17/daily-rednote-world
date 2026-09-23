from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .content_artifacts import knowledge_markdown


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def archive_knowledge(
    root: Path,
    topic: dict[str, Any],
    package: dict[str, Any],
    draft: dict[str, Any],
    output_dir: Path,
    allow_update: bool = False,
) -> Path:
    if package.get("fixture_mode"):
        raise ValueError("fixture 不得写入正式知识库")
    places = root / "knowledge" / "places"
    entry_path = places / f"{topic['slug']}.md"
    entry_text = knowledge_markdown(package, draft)
    if entry_path.exists():
        if entry_path.read_text(encoding="utf-8") != entry_text:
            if not allow_update:
                raise FileExistsError(f"知识档案已存在且内容不同：{entry_path}")
            _write_atomic(entry_path, entry_text)
    else:
        _write_atomic(entry_path, entry_text)

    index_path = root / "knowledge" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"version": 1, "places": []}
    entry = {
        "slug": topic["slug"],
        "name_zh": package["location"]["name_zh"],
        "name_en": package["location"]["name_en"],
        "country": package["location"]["country_zh"],
        "continent": package["location"]["continent"],
        "location_type": package["location"]["location_type"],
        "themes": topic["themes"],
        "coordinates": [package["location"]["latitude"], package["location"]["longitude"]],
        "research_date": package["research_date"],
        "file": entry_path.relative_to(root).as_posix(),
        "content_output": output_dir.resolve().as_posix(),
    }
    existing = next(
        (item for item in index["places"] if item["slug"] == topic["slug"]), None
    )
    if existing:
        if existing != entry:
            if not allow_update:
                raise ValueError(f"知识索引中 {topic['slug']} 已存在且内容不同")
            index["places"] = [
                entry if item["slug"] == topic["slug"] else item
                for item in index["places"]
            ]
            index["places"].sort(
                key=lambda item: (item["continent"], item["country"], item["name_zh"])
            )
            _write_atomic(index_path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
        return entry_path
    index["places"].append(entry)
    index["places"].sort(key=lambda item: (item["continent"], item["country"], item["name_zh"]))
    _write_atomic(index_path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return entry_path


def search_knowledge(
    root: Path,
    country: str | None = None,
    continent: str | None = None,
    location_type: str | None = None,
    theme: str | None = None,
) -> list[dict[str, Any]]:
    path = root / "knowledge" / "index.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))["places"]
    return [
        row for row in rows
        if (not country or row["country"] == country)
        and (not continent or row["continent"] == continent)
        and (not location_type or row["location_type"] == location_type)
        and (not theme or theme in row["themes"])
    ]
