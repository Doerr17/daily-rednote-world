from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from .config import load_config
from .content import run_content
from .content_store import persist_content, recent_content_texts
from .content_validation import load_audited_research
from .daily import STAGES, run_daily, run_fixture_daily
from .db import connect, migrate, utc_now
from .doctor import run_doctor
from .fetcher import FixtureFetcher, HttpFetcher
from .knowledge import archive_knowledge, search_knowledge
from .maps import ensure_map_data
from .media import CommonsClient, create_fixture_media, download_approved_candidate
from .operations import add_metrics, list_metrics, publish_run
from .providers import (
    CodexResearchProvider,
    CodexWritingProvider,
    FixtureResearchProvider,
    FixtureWritingProvider,
)
from .research import ResearchGateError, run_research
from .research_store import persist_research
from .render import render_cards
from .seeds import import_seeds
from .selector import choose_topic, get_topic_by_id, recent_history, record_selection


ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="每日探索世界内容生产项目")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="检查选题数据库、种子和基础环境")

    topics = commands.add_parser("topics", help="查看候选地点与选题")
    topics.add_argument("--all", action="store_true", help="包含已经选择过的地点")
    topics.add_argument("--json", action="store_true", help="输出 JSON")

    history = commands.add_parser("history", help="查看选题历史")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true", help="输出 JSON")

    select = commands.add_parser("select", help="只运行选题阶段；默认不写入历史")
    select.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD")
    select.add_argument("--seed", default="default", help="确定性并列选择种子")
    select.add_argument("--commit", action="store_true", help="确认写入选题历史")
    select.add_argument("--json", action="store_true", help="输出 JSON")

    research = commands.add_parser("research", help="运行研究与独立事实核查阶段")
    research.add_argument("--topic", help="真实研究使用的候选选题 ID")
    research.add_argument("--provider", choices=["codex", "fixture"], default="codex")
    research.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD")
    research.add_argument("--output", help="自定义研究输出目录")
    research.add_argument("--force", action="store_true", help="覆盖已有的同名研究输出")

    write = commands.add_parser("write", help="从已通过审计的研究包生成文案与知识档案")
    write.add_argument("--research", help="包含研究包与审计文件的目录")
    write.add_argument("--provider", choices=["codex", "fixture"], default="codex")
    write.add_argument("--output", help="自定义文案输出目录")
    write.add_argument("--force", action="store_true", help="覆盖已有的同名文案输出")

    knowledge = commands.add_parser("knowledge", help="检索正式世界地理与人文知识库")
    knowledge.add_argument("--country")
    knowledge.add_argument("--continent")
    knowledge.add_argument("--type", dest="location_type")
    knowledge.add_argument("--theme")
    knowledge.add_argument("--json", action="store_true")

    media_search = commands.add_parser("media-search", help="检索 Commons 图片及授权/地点证据")
    media_search.add_argument("--research", required=True, help="已通过审计的研究目录")
    media_search.add_argument("--query", help="检索词；默认使用地点英文名")
    media_search.add_argument("--output", help="候选 JSON 输出路径")

    media_download = commands.add_parser("media-download", help="下载已自动核验的 Commons 候选")
    media_download.add_argument("--candidates", required=True, help="media-search 生成的候选 JSON")
    media_download.add_argument("--index", required=True, type=int, help="从 1 开始的候选序号")
    media_download.add_argument("--output", required=True, help="素材输出目录")

    commands.add_parser("map-data", help="下载或核验固定版本的 Natural Earth 地图数据")

    render = commands.add_parser("render", help="生成 1080×1440 图文卡片并执行视觉 QA")
    render.add_argument("--research", help="已通过审计的研究目录")
    render.add_argument("--content", help="已通过审计的文案目录")
    render.add_argument("--media-manifest", help="正式渲染使用的已核验媒体清单")
    render.add_argument("--provider", choices=["real", "fixture"], default="real")
    render.add_argument("--output", help="渲染输出目录")
    render.add_argument("--force", action="store_true", help="覆盖已有渲染输出")

    daily = commands.add_parser("daily", help="运行可恢复的每日完整生产流程")
    daily.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD")
    daily.add_argument("--seed", default="default", help="确定性选题种子")
    daily.add_argument("--provider", choices=["codex", "fixture"], default="codex")
    daily.add_argument(
        "--restart-stage", choices=STAGES[1:-1], help="重做指定阶段及其下游"
    )

    publish = commands.add_parser("publish", help="人工审核并真实发布后登记状态")
    publish.add_argument("date", help="YYYY-MM-DD")
    publish.add_argument("--published-at", help="ISO 8601；默认当前 UTC 时间")
    publish.add_argument("--url", help="真实小红书笔记链接")
    publish.add_argument("--confirm-reviewed", action="store_true")

    metrics = commands.add_parser("metrics", help="人工录入或查看真实运营数据")
    metric_commands = metrics.add_subparsers(dest="metrics_command", required=True)
    metrics_add = metric_commands.add_parser("add")
    metrics_add.add_argument("date", help="已发布内容日期")
    metrics_add.add_argument("--as-of", required=True, help="数据快照时间")
    metrics_add.add_argument("--views", required=True, type=int)
    metrics_add.add_argument("--likes", required=True, type=int)
    metrics_add.add_argument("--saves", required=True, type=int)
    metrics_add.add_argument("--comments", required=True, type=int)
    metrics_add.add_argument("--followers-gained", required=True, type=int)
    metrics_list = metric_commands.add_parser("list")
    metrics_list.add_argument("--date")
    metrics_list.add_argument("--json", action="store_true")
    return parser


def _validate_day(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"无效日期：{value}；应为 YYYY-MM-DD") from exc


def _open_project():
    config = load_config(ROOT)
    connection = connect(config.database)
    migrate(connection, config.migrations)
    import_seeds(connection, config.seed_file)
    return config, connection


def _topics(connection, include_used: bool) -> list[dict[str, Any]]:
    used_clause = "" if include_used else "AND s.id IS NULL"
    rows = connection.execute(
        f"""
        SELECT t.topic_id, l.name_zh, l.name_en, l.country_zh, l.continent,
               l.location_type, t.hook_zh, s.selected_on, s.status
        FROM topic_candidates t
        JOIN locations l ON l.id=t.location_id
        LEFT JOIN selections s ON s.topic_id=t.id AND s.status != 'failed'
        WHERE t.active=1 AND l.active=1 {used_clause}
        ORDER BY l.continent, l.country_zh, t.topic_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _history(connection, limit: int) -> list[dict[str, Any]]:
    return recent_history(connection, limit)


def _print_rows(rows: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("（暂无记录）")
        return
    for row in rows:
        if "selected_on" in row and "hook_zh" in row and "topic_id" in row:
            print(
                f"{row.get('selected_on') or '-'}\t{row.get('status') or 'available'}\t"
                f"{row.get('name_zh', '')}\t{row['hook_zh']}"
            )


def _selection_payload(selection, day: str, committed: bool) -> dict[str, Any]:
    topic = selection.topic
    return {
        "date": day,
        "mode": "committed" if committed else "dry-run",
        "topic_id": topic["topic_id"],
        "location": {
            "name_zh": topic["name_zh"],
            "name_en": topic["name_en"],
            "country": topic["country_zh"],
            "continent": topic["continent"],
            "type": topic["location_type"],
        },
        "hook": topic["hook_zh"],
        "angle": topic["angle_zh"],
        "themes": topic["themes"],
        "visual_tags": topic["visual_tags"],
        "score": round(selection.score, 4),
        "duplicate_score": round(selection.duplicate_score, 4),
        "score_details": {key: round(value, 4) for key, value in selection.details.items()},
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    connection = None
    try:
        config, connection = _open_project()
        if args.command == "doctor":
            checks = run_doctor(connection, config)
            for name, ok, detail in checks:
                print(f"{'OK' if ok else 'FAIL'}\t{name}\t{detail}")
            return 0 if all(ok for _, ok, _ in checks) else 1
        if args.command == "daily":
            run_date = _validate_day(args.date)
            if args.provider == "fixture":
                output = run_fixture_daily(
                    ROOT, config, run_date, restart_stage=args.restart_stage
                )
            else:
                output = run_daily(
                    ROOT,
                    connection,
                    config,
                    run_date,
                    args.seed,
                    restart_stage=args.restart_stage,
                )
            print(f"每日内容包：{output}")
            print("状态：awaiting_review；必须人工审核并手动发布")
            return 0
        if args.command == "publish":
            run_date = _validate_day(args.date)
            publication_id = publish_run(
                connection,
                run_date,
                args.published_at or utc_now(),
                args.url,
                args.confirm_reviewed,
            )
            print(f"已登记人工发布：{publication_id}；日期：{run_date}")
            return 0
        if args.command == "metrics":
            if args.metrics_command == "add":
                run_date = _validate_day(args.date)
                metric_id = add_metrics(
                    connection,
                    run_date,
                    args.as_of,
                    args.views,
                    args.likes,
                    args.saves,
                    args.comments,
                    args.followers_gained,
                )
                print(f"已记录真实运营数据：{metric_id}")
                return 0
            rows = list_metrics(
                connection, _validate_day(args.date) if args.date else None
            )
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif not rows:
                print("（暂无运营数据）")
            else:
                for row in rows:
                    print(
                        f"{row['run_date']}\t{row['as_of']}\t阅读 {row['views']}\t"
                        f"赞 {row['likes']}\t藏 {row['saves']}\t评 {row['comments']}\t"
                        f"关注 +{row['followers_gained']}"
                    )
            return 0
        if args.command == "topics":
            _print_rows(_topics(connection, args.all), args.json)
            return 0
        if args.command == "history":
            _print_rows(_history(connection, args.limit), args.json)
            return 0
        if args.command == "knowledge":
            rows = search_knowledge(
                ROOT, args.country, args.continent, args.location_type, args.theme
            )
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif not rows:
                print("（暂无匹配的正式知识档案）")
            else:
                for row in rows:
                    print(
                        f"{row['name_zh']}\t{row['country']}\t{row['continent']}\t"
                        f"{row['location_type']}\t{row['file']}"
                    )
            return 0
        if args.command == "map-data":
            path, metadata = ensure_map_data(ROOT, config.map)
            print(f"地图数据：{path}")
            print(f"版本：{metadata['version']}；SHA-256：{metadata['sha256']}")
            return 0
        if args.command == "media-search":
            research_directory = Path(args.research).resolve()
            package, _, _ = load_audited_research(research_directory)
            location = package["location"]
            aliases = [location["name_zh"], location["name_en"]]
            query = args.query or location["name_en"]
            candidates = CommonsClient(config.media).search(
                query, aliases, float(location["latitude"]), float(location["longitude"])
            )
            payload = {
                "schema_version": "1.0",
                "topic_id": package["topic_id"],
                "fixture_mode": package["fixture_mode"],
                "query": query,
                "candidates": candidates,
            }
            output = (
                Path(args.output).resolve()
                if args.output
                else research_directory / "media_candidates.json"
            )
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            verified = sum(
                item["rights_status"] == "complete"
                and item["location_match_status"] == "verified"
                for item in candidates
            )
            print(f"候选：{len(candidates)}；可自动核验：{verified}；输出：{output}")
            return 0
        if args.command == "media-download":
            payload = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
            if payload.get("fixture_mode"):
                raise ValueError("media-download 不接受 fixture 候选")
            index = args.index - 1
            if index < 0 or index >= len(payload.get("candidates", [])):
                raise ValueError("候选序号超出范围")
            output_dir = Path(args.output).resolve()
            candidate = payload["candidates"][index]
            suffix = ".png" if candidate.get("mime") == "image/png" else ".jpg"
            asset = download_approved_candidate(
                candidate, output_dir / f"commons-{args.index}{suffix}", config.media
            )
            manifest_path = output_dir / "media_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("topic_id") != payload["topic_id"]:
                    raise ValueError("现有媒体清单属于其他 topic_id")
            else:
                manifest = {
                    "schema_version": "1.0",
                    "topic_id": payload["topic_id"],
                    "fixture_mode": False,
                    "assets": [],
                }
            if any(item["sha256"] == asset["sha256"] for item in manifest["assets"]):
                raise ValueError("该图片已存在于媒体清单")
            manifest["assets"].append(asset)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"已核验并下载：{asset['local_path']}")
            print(f"媒体清单：{manifest_path}")
            return 0
        if args.command == "select":
            day = _validate_day(args.date)
            selection = choose_topic(
                connection,
                day,
                args.seed,
                config.recent_history_limit,
                config.hard_similarity_threshold,
                config.selection_weights,
            )
            if args.commit:
                record_selection(connection, day, selection)
            payload = _selection_payload(selection, day, args.commit)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"模式：{'已写入历史' if args.commit else 'dry-run（未写入历史）'}")
                print(f"地点：{payload['location']['name_zh']} / {payload['location']['name_en']}")
                print(f"角度：{payload['hook']}")
                print(f"总分：{payload['score']:.4f}；重复度：{payload['duplicate_score']:.4f}")
            return 0
        if args.command == "research":
            research_date = _validate_day(args.date)
            fixture_dir = ROOT / "tests" / "fixtures" / "research"
            if args.provider == "fixture":
                if args.topic:
                    raise ValueError("fixture 模式使用专用测试地点，不接受 --topic")
                provider = FixtureResearchProvider(fixture_dir)
                fetcher = FixtureFetcher(fixture_dir)
                topic = None
                default_output = ROOT / "output" / "_fixtures" / "research"
            else:
                if not args.topic:
                    raise ValueError("codex 研究必须提供 --topic")
                topic = get_topic_by_id(connection, args.topic)
                if topic is None:
                    raise ValueError(f"未知候选选题：{args.topic}")
                provider = CodexResearchProvider(
                    ROOT, int(config.research["codex_timeout_seconds"])
                )
                fetcher = HttpFetcher(
                    ROOT / config.research["cache_directory"],
                    str(config.research["user_agent"]),
                    float(config.research["request_timeout_seconds"]),
                    int(config.research["request_retries"]),
                    float(config.research["minimum_host_interval_seconds"]),
                )
                default_output = ROOT / "output" / "_research" / f"{research_date}-{args.topic}"
            destination = Path(args.output).resolve() if args.output else default_output
            output, package, audit = run_research(
                ROOT,
                destination,
                provider,
                fetcher,
                topic,
                research_date,
                float(config.research["coordinate_tolerance_km"]),
                int(config.research["minimum_sources"]),
                force=args.force,
            )
            if args.provider != "fixture":
                run_id = persist_research(
                    connection,
                    topic["id"],
                    args.provider,
                    package,
                    audit,
                    str(output),
                )
                print(f"研究运行 ID：{run_id}")
            print(f"研究完成：{output}")
            print("状态：事实门禁通过；尚未进入文案阶段")
            return 0
        if args.command == "write":
            fixture_dir = ROOT / "tests" / "fixtures" / "content"
            if args.provider == "fixture":
                research_directory = (
                    Path(args.research).resolve()
                    if args.research
                    else ROOT / "output" / "_fixtures" / "research"
                )
                provider = FixtureWritingProvider(fixture_dir)
                default_output = ROOT / "output" / "_fixtures" / "content"
                recent = []
            else:
                if not args.research:
                    raise ValueError("codex 写作必须提供 --research")
                research_directory = Path(args.research).resolve()
                provider = CodexWritingProvider(
                    ROOT, int(config.research["codex_timeout_seconds"])
                )
                default_output = ROOT / "output" / "_content" / research_directory.name
                recent = recent_content_texts(
                    connection, int(config.content["recent_content_limit"])
                )
            destination = Path(args.output).resolve() if args.output else default_output
            output, package, draft, audit, content_sha256 = run_content(
                ROOT,
                research_directory,
                destination,
                provider,
                config.content,
                recent,
                force=args.force,
            )
            if args.provider != "fixture":
                topic = get_topic_by_id(connection, package["topic_id"])
                if topic is None:
                    raise ValueError(f"研究包 topic_id 不在候选库：{package['topic_id']}")
                run_id = persist_content(
                    connection,
                    topic["id"],
                    args.provider,
                    package,
                    draft,
                    audit,
                    str(output),
                    content_sha256,
                )
                knowledge_path = archive_knowledge(ROOT, topic, package, draft, output)
                print(f"文案运行 ID：{run_id}")
                print(f"知识档案：{knowledge_path}")
            print(f"文案完成：{output}")
            print("状态：内容门禁通过；尚未进入图片、地图与排版阶段")
            return 0
        if args.command == "render":
            if args.provider == "fixture":
                research_directory = (
                    Path(args.research).resolve()
                    if args.research
                    else ROOT / "output" / "_fixtures" / "research"
                )
                content_directory = (
                    Path(args.content).resolve()
                    if args.content
                    else ROOT / "output" / "_fixtures" / "content"
                )
                content_package = json.loads(
                    (content_directory / "content_package.json").read_text(encoding="utf-8")
                )
                media_manifest = create_fixture_media(
                    ROOT / "output" / "_fixtures" / "media",
                    content_package["topic_id"],
                )
                default_output = ROOT / "output" / "_fixtures" / "rendered"
            else:
                if not args.research or not args.content or not args.media_manifest:
                    raise ValueError("正式渲染必须提供 --research、--content 和 --media-manifest")
                research_directory = Path(args.research).resolve()
                content_directory = Path(args.content).resolve()
                media_manifest = json.loads(
                    Path(args.media_manifest).read_text(encoding="utf-8")
                )
                default_output = ROOT / "output" / "_rendered" / content_directory.name
            destination = Path(args.output).resolve() if args.output else default_output
            output, audit = render_cards(
                ROOT,
                research_directory,
                content_directory,
                media_manifest,
                destination,
                config.map,
                config.render,
                config.media,
                force=args.force,
            )
            print(f"渲染完成：{output}")
            print(f"卡片：{len(audit['image_checks'])}；视觉 QA：通过")
            return 0
        return 0
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
