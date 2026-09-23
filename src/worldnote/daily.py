from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .content import run_content
from .content_store import persist_content, recent_content_texts
from .db import utc_now
from .fetcher import FixtureFetcher, HttpFetcher
from .knowledge import archive_knowledge
from .media import CommonsClient, create_fixture_media, download_approved_candidate
from .providers import (
    CachedResearchProvider,
    CodexResearchProvider,
    CodexWritingProvider,
    FixtureResearchProvider,
    FixtureWritingProvider,
)
from .research import run_research
from .research_store import persist_research
from .render import render_cards
from .selector import choose_topic, get_topic_by_id, record_selection


STAGES = [
    "selected",
    "researched",
    "drafted",
    "assets_ready",
    "rendered",
    "qa_passed",
    "awaiting_review",
]


class DailyError(RuntimeError):
    pass


def _log(path: Path, event: str, **details: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"time": utc_now(), "event": event, **details}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _safe_remove(path: Path, allowed_root: Path) -> None:
    resolved = path.resolve()
    allowed = allowed_root.resolve()
    if resolved == allowed or allowed not in resolved.parents:
        raise DailyError(f"拒绝删除工作区之外的路径：{resolved}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def _verify_manifest(directory: Path) -> bool:
    path = directory / "manifest.json"
    if not path.is_file():
        return False
    try:
        files = json.loads(path.read_text(encoding="utf-8"))["files"]
        return all(
            (directory / name).is_file()
            and hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest
            for name, digest in files.items()
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return False


def _assemble_final(
    final_directory: Path,
    research_directory: Path,
    content_directory: Path,
    rendered_directory: Path,
) -> Path:
    if final_directory.exists():
        raise FileExistsError(f"最终日期目录已存在：{final_directory}")
    final_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{final_directory.name}-", dir=final_directory.parent)
    )
    try:
        copies = {
            research_directory / "research.md": temporary / "research.md",
            research_directory / "sources.md": temporary / "sources.md",
            research_directory / "location.json": temporary / "location.json",
            research_directory / "audit.json": temporary / "research_audit.json",
            research_directory / "research_package.json": temporary / "research_package.json",
            content_directory / "learning_notes.md": temporary / "learning_notes.md",
            content_directory / "content.md": temporary / "content.md",
            content_directory / "cards.json": temporary / "cards.json",
            content_directory / "content_package.json": temporary / "content_package.json",
            content_directory / "content_audit.json": temporary / "content_audit.json",
            rendered_directory / "render_audit.json": temporary / "render_audit.json",
            rendered_directory / "image_sources.md": temporary / "image_sources.md",
            rendered_directory / "review_checklist.md": temporary / "review_checklist.md",
        }
        for source, target in copies.items():
            if not source.is_file():
                raise DailyError(f"组装最终内容包时缺少文件：{source}")
            shutil.copy2(source, target)
        shutil.copytree(rendered_directory / "images", temporary / "images")
        shutil.copytree(rendered_directory / "assets", temporary / "assets")
        media_manifest = json.loads(
            (rendered_directory / "assets" / "media_manifest.json").read_text(encoding="utf-8")
        )
        image_credits = [
            f"- 第{'、'.join(str(number) for number in asset['used_on'])}张："
            f"{asset['creator']}（{asset['license']}）"
            for asset in media_manifest.get("assets", [])
            if asset.get("used_on")
        ]
        if image_credits:
            credit_section = "\n\n图片来源与许可\n" + "\n".join(image_credits) + "\n"
            path = temporary / "content.md"
            path.write_text(
                path.read_text(encoding="utf-8").rstrip() + credit_section,
                encoding="utf-8",
            )
        audit = {
            "audit_version": "1.0",
            "passed": True,
            "components": {
                "research": "research_audit.json",
                "content": "content_audit.json",
                "render": "render_audit.json",
            },
            "publication_status": "awaiting_review",
        }
        (temporary / "audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (temporary / "manifest.json").write_text(
            json.dumps({"files": _hashes(temporary)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, final_directory)
        return final_directory
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _checkpoint(
    connection: sqlite3.Connection, run_id: int, stage: str, artifact_dir: Path
) -> None:
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO daily_checkpoints(
                daily_run_id, stage, artifact_dir, completed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (run_id, stage, str(artifact_dir), utc_now()),
        )
        connection.execute(
            "UPDATE daily_runs SET status=?, error=NULL, updated_at=? WHERE id=?",
            (stage, utc_now(), run_id),
        )


def _completed(connection: sqlite3.Connection, run_id: int) -> set[str]:
    return {
        row["stage"]
        for row in connection.execute(
            "SELECT stage FROM daily_checkpoints WHERE daily_run_id=?", (run_id,)
        )
    }


def _restart(
    connection: sqlite3.Connection,
    run: sqlite3.Row,
    stage: str,
    workspace: Path,
    final_directory: Path,
) -> None:
    if stage not in STAGES[1:-1]:
        raise DailyError("--restart-stage 只允许 researched 到 qa_passed")
    if run["status"] == "published":
        raise DailyError("已发布运行不得自动重做")
    start = STAGES.index(stage)
    affected = STAGES[start:]
    directories = {
        "researched": workspace / "research",
        "drafted": workspace / "content",
        "assets_ready": workspace / "media",
        "rendered": workspace / "rendered",
        "qa_passed": final_directory,
    }
    for current in affected:
        path = directories.get(current)
        if path and path.exists():
            allowed = final_directory.parent if path == final_directory else workspace
            _safe_remove(path, allowed)
    placeholders = ",".join("?" for _ in affected)
    previous = STAGES[start - 1]
    with connection:
        connection.execute(
            f"DELETE FROM daily_checkpoints WHERE daily_run_id=? AND stage IN ({placeholders})",
            (run["id"], *affected),
        )
        connection.execute(
            "UPDATE daily_runs SET status=?, error=NULL, updated_at=? WHERE id=?",
            (previous, utc_now(), run["id"]),
        )


def _auto_media(
    config: ProjectConfig,
    package: dict[str, Any],
    topic: dict[str, Any],
    directory: Path,
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    location = package["location"]
    aliases = list(dict.fromkeys(topic["aliases"] + [location["name_zh"], location["name_en"]]))
    client = CommonsClient(config.media)
    candidates = client.search(
        location["name_en"], aliases, float(location["latitude"]), float(location["longitude"])
    )
    candidate_payload = {
        "schema_version": "1.0",
        "topic_id": package["topic_id"],
        "fixture_mode": False,
        "query": location["name_en"],
        "candidates": candidates,
    }
    (directory / "media_candidates.json").write_text(
        json.dumps(candidate_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assets: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, 1):
        if candidate["rights_status"] != "complete" or candidate["location_match_status"] != "verified":
            continue
        suffix = ".png" if candidate.get("mime") == "image/png" else ".jpg"
        try:
            asset = download_approved_candidate(
                candidate, directory / f"commons-{index}{suffix}", config.media
            )
        except Exception:
            continue
        if all(item["sha256"] != asset["sha256"] for item in assets):
            assets.append(asset)
        if len(assets) >= int(config.media["minimum_assets"]):
            break
    if len(assets) < int(config.media["minimum_assets"]):
        raise DailyError(
            f"只有 {len(assets)} 张图片通过授权、地点和清晰度门禁；候选已保留供人工检查"
        )
    manifest = {
        "schema_version": "1.0",
        "topic_id": package["topic_id"],
        "fixture_mode": False,
        "assets": assets,
    }
    (directory / "media_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def run_daily(
    root: Path,
    connection: sqlite3.Connection,
    config: ProjectConfig,
    run_date: str,
    seed: str,
    restart_stage: str | None = None,
) -> Path:
    run = connection.execute(
        "SELECT * FROM daily_runs WHERE run_date=?", (run_date,)
    ).fetchone()
    if run is None:
        selected = connection.execute(
            "SELECT topic_id FROM selections WHERE selected_on=?", (run_date,)
        ).fetchone()
        if selected is None:
            selection = choose_topic(
                connection,
                run_date,
                seed,
                config.recent_history_limit,
                config.hard_similarity_threshold,
                config.selection_weights,
            )
            record_selection(connection, run_date, selection)
            topic_row_id = selection.topic["id"]
        else:
            topic_row_id = int(selected["topic_id"])
        run_uuid = str(uuid.uuid4())
        workspace = root / "output" / "_runs" / f"{run_date}-{run_uuid[:8]}"
        final_directory = root / "output" / run_date
        now = utc_now()
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO daily_runs(
                    run_id, run_date, topic_id, provider, status, workspace_dir,
                    final_dir, error, created_at, updated_at
                ) VALUES (?, ?, ?, 'codex', 'selected', ?, ?, NULL, ?, ?)
                """,
                (
                    run_uuid, run_date, topic_row_id, str(workspace),
                    str(final_directory), now, now,
                ),
            )
        workspace.mkdir(parents=True, exist_ok=True)
        _checkpoint(connection, int(cursor.lastrowid), "selected", workspace)
        run = connection.execute("SELECT * FROM daily_runs WHERE id=?", (cursor.lastrowid,)).fetchone()
    if run["status"] == "published":
        raise DailyError(f"{run_date} 已发布，不会重跑")
    if run["status"] == "awaiting_review" and not restart_stage:
        return Path(run["final_dir"])
    workspace = Path(run["workspace_dir"])
    final_directory = Path(run["final_dir"])
    log_path = root / "logs" / f"daily-{run['run_id']}.jsonl"
    if restart_stage:
        _restart(connection, run, restart_stage, workspace, final_directory)
    topic_key = connection.execute(
        "SELECT topic_id FROM topic_candidates WHERE id=?", (run["topic_id"],)
    ).fetchone()
    topic = get_topic_by_id(connection, topic_key["topic_id"]) if topic_key else None
    if topic is None:
        raise DailyError("daily 运行关联的候选选题不存在")
    completed = _completed(connection, run["id"])
    _log(log_path, "daily_resumed", run_date=run_date, completed=sorted(completed))
    try:
        research_directory = workspace / "research"
        if "researched" not in completed:
            cached_research = os.environ.get("WORLDNOTE_CACHED_RESEARCH")
            if cached_research:
                provider = CachedResearchProvider(
                    root,
                    Path(cached_research).resolve(),
                    int(config.research["codex_timeout_seconds"]),
                )
            else:
                provider = CodexResearchProvider(
                    root, int(config.research["codex_timeout_seconds"])
                )
            fetcher = HttpFetcher(
                root / config.research["cache_directory"],
                str(config.research["user_agent"]),
                float(config.research["request_timeout_seconds"]),
                int(config.research["request_retries"]),
                float(config.research["minimum_host_interval_seconds"]),
            )
            run_research(
                root, research_directory, provider, fetcher, topic, run_date,
                float(config.research["coordinate_tolerance_km"]),
                int(config.research["minimum_sources"]),
            )
            _checkpoint(connection, run["id"], "researched", research_directory)
            connection.execute(
                "UPDATE selections SET status='researched' WHERE selected_on=?", (run_date,)
            )
            connection.commit()
            _log(log_path, "stage_complete", stage="researched")
        content_directory = workspace / "content"
        if "drafted" not in completed:
            run_content(
                root,
                research_directory,
                content_directory,
                CodexWritingProvider(root, int(config.research["codex_timeout_seconds"])),
                config.content,
                recent_content_texts(connection, int(config.content["recent_content_limit"])),
            )
            _checkpoint(connection, run["id"], "drafted", content_directory)
            connection.execute(
                "UPDATE selections SET status='drafted' WHERE selected_on=?", (run_date,)
            )
            connection.commit()
            _log(log_path, "stage_complete", stage="drafted")
        media_directory = workspace / "media"
        if "assets_ready" not in completed:
            package = json.loads(
                (research_directory / "research_package.json").read_text(encoding="utf-8")
            )
            _auto_media(config, package, topic, media_directory)
            _checkpoint(connection, run["id"], "assets_ready", media_directory)
            _log(log_path, "stage_complete", stage="assets_ready")
        rendered_directory = workspace / "rendered"
        if "rendered" not in completed:
            media_manifest = json.loads(
                (media_directory / "media_manifest.json").read_text(encoding="utf-8")
            )
            render_cards(
                root, research_directory, content_directory, media_manifest,
                rendered_directory, config.map, config.render, config.media,
            )
            _checkpoint(connection, run["id"], "rendered", rendered_directory)
            _log(log_path, "stage_complete", stage="rendered")
        if "qa_passed" not in completed:
            if final_directory.exists():
                if not _verify_manifest(final_directory):
                    raise DailyError("最终目录已存在但 manifest 校验失败，需显式重启 qa_passed")
            else:
                _assemble_final(final_directory, research_directory, content_directory, rendered_directory)
            _checkpoint(connection, run["id"], "qa_passed", final_directory)
            _log(log_path, "stage_complete", stage="qa_passed")
        if "awaiting_review" not in completed:
            package = json.loads(
                (research_directory / "research_package.json").read_text(encoding="utf-8")
            )
            research_audit = json.loads(
                (research_directory / "audit.json").read_text(encoding="utf-8")
            )
            content_package = json.loads(
                (content_directory / "content_package.json").read_text(encoding="utf-8")
            )
            content_audit = json.loads(
                (content_directory / "content_audit.json").read_text(encoding="utf-8")
            )
            if connection.execute(
                "SELECT 1 FROM research_runs WHERE topic_id=? AND research_date=?",
                (topic["id"], run_date),
            ).fetchone() is None:
                persist_research(
                    connection, topic["id"], "codex", package, research_audit,
                    str(research_directory),
                )
            if connection.execute(
                "SELECT 1 FROM content_runs WHERE topic_id=? AND content_date=?",
                (topic["id"], run_date),
            ).fetchone() is None:
                persist_content(
                    connection, topic["id"], "codex", package, content_package,
                    content_audit, str(content_directory),
                    content_audit["content_package_sha256"],
                )
            archive_knowledge(
                root, topic, package, content_package, final_directory, allow_update=True
            )
            _checkpoint(connection, run["id"], "awaiting_review", final_directory)
            connection.execute(
                "UPDATE selections SET status='awaiting_review' WHERE selected_on=?",
                (run_date,),
            )
            connection.commit()
            _log(log_path, "daily_complete", status="awaiting_review")
        return final_directory
    except Exception as exc:
        with connection:
            connection.execute(
                "UPDATE daily_runs SET status='failed', error=?, updated_at=? WHERE id=?",
                (str(exc), utc_now(), run["id"]),
            )
            connection.execute(
                "UPDATE selections SET status='failed' WHERE selected_on=?", (run_date,)
            )
        _log(log_path, "daily_failed", error=str(exc))
        raise


def run_fixture_daily(
    root: Path,
    config: ProjectConfig,
    run_date: str,
    restart_stage: str | None = None,
    fail_after: str | None = None,
) -> Path:
    base = root / "output" / "_fixtures" / "daily"
    workspace = base / "workspace"
    final_directory = base / "final"
    state_path = base / "run_state.json"
    log_path = root / "logs" / "daily-fixture.jsonl"
    base.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"run_date": run_date, "status": "selected", "completed": ["selected"]}
    if state["run_date"] != run_date:
        raise DailyError("fixture daily 已有其他日期状态；请显式重启 researched")
    if restart_stage:
        if restart_stage not in STAGES[1:-1]:
            raise DailyError("--restart-stage 只允许 researched 到 qa_passed")
        start = STAGES.index(restart_stage)
        state["completed"] = [item for item in state["completed"] if STAGES.index(item) < start]
        state["status"] = STAGES[start - 1]
        mapping = {
            "researched": workspace / "research",
            "drafted": workspace / "content",
            "assets_ready": workspace / "media",
            "rendered": workspace / "rendered",
            "qa_passed": final_directory,
        }
        for current in STAGES[start:]:
            path = mapping.get(current)
            if path and path.exists():
                _safe_remove(path, base)
    workspace.mkdir(parents=True, exist_ok=True)

    def save(stage: str) -> None:
        if stage not in state["completed"]:
            state["completed"].append(stage)
        state["status"] = stage
        state["updated_at"] = utc_now()
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _log(log_path, "fixture_stage_complete", stage=stage)
        if fail_after == stage:
            raise DailyError(f"fixture injected failure after {stage}")

    try:
        research_directory = workspace / "research"
        if "researched" not in state["completed"]:
            run_research(
                root,
                research_directory,
                FixtureResearchProvider(root / "tests" / "fixtures" / "research"),
                FixtureFetcher(root / "tests" / "fixtures" / "research"),
                None,
                run_date,
                float(config.research["coordinate_tolerance_km"]),
                int(config.research["minimum_sources"]),
            )
            save("researched")
        content_directory = workspace / "content"
        if "drafted" not in state["completed"]:
            run_content(
                root,
                research_directory,
                content_directory,
                FixtureWritingProvider(root / "tests" / "fixtures" / "content"),
                config.content,
            )
            save("drafted")
        media_directory = workspace / "media"
        if "assets_ready" not in state["completed"]:
            content = json.loads(
                (content_directory / "content_package.json").read_text(encoding="utf-8")
            )
            media_manifest = create_fixture_media(media_directory, content["topic_id"])
            (media_directory / "media_manifest.json").write_text(
                json.dumps(media_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            save("assets_ready")
        else:
            media_manifest = json.loads(
                (media_directory / "media_manifest.json").read_text(encoding="utf-8")
            )
        rendered_directory = workspace / "rendered"
        if "rendered" not in state["completed"]:
            render_cards(
                root,
                research_directory,
                content_directory,
                media_manifest,
                rendered_directory,
                config.map,
                config.render,
                config.media,
            )
            save("rendered")
        if "qa_passed" not in state["completed"]:
            if final_directory.exists():
                if not _verify_manifest(final_directory):
                    raise DailyError("fixture 最终目录 manifest 校验失败，需显式重启 qa_passed")
            else:
                _assemble_final(final_directory, research_directory, content_directory, rendered_directory)
            save("qa_passed")
        if "awaiting_review" not in state["completed"]:
            save("awaiting_review")
        return final_directory
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = str(exc)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _log(log_path, "fixture_daily_failed", error=str(exc))
        raise
