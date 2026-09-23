from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    database: Path
    migrations: Path
    seed_file: Path
    recent_history_limit: int
    hard_similarity_threshold: float
    minimum_seed_locations: int
    research: dict[str, Any]
    content: dict[str, Any]
    media: dict[str, Any]
    map: dict[str, Any]
    render: dict[str, Any]
    selection_weights: dict[str, float]


def load_config(root: Path) -> ProjectConfig:
    data: dict[str, Any] = json.loads(
        (root / "config" / "project.json").read_text(encoding="utf-8")
    )
    return ProjectConfig(
        root=root,
        database=root / data["database"],
        migrations=root / data["migrations"],
        seed_file=root / data["seed_file"],
        recent_history_limit=int(data["recent_history_limit"]),
        hard_similarity_threshold=float(data["hard_similarity_threshold"]),
        minimum_seed_locations=int(data["minimum_seed_locations"]),
        research=data["research"],
        content=data["content"],
        media=data["media"],
        map=data["map"],
        render=data["render"],
        selection_weights={
            key: float(value) for key, value in data["selection_weights"].items()
        },
    )
