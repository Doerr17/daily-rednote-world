from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from playwright.sync_api import sync_playwright

from .content_validation import ContentValidationError, load_audited_research
from .maps import ensure_map_data, generate_locator_map


class RenderError(RuntimeError):
    pass


FILENAMES = {
    "cover": "01_cover.png",
    "location": "02_location.png",
    "feature": "03_landscape.png",
    "story": "04_culture.png",
    "facts": "05_facts.png",
    "summary": "06_summary.png",
}

ROLE_LABELS = {
    "cover": "PLACE OF THE DAY",
    "feature": "LANDSCAPE & FORM",
    "story": "HISTORY & PEOPLE",
    "facts": "DETAILS TO REMEMBER",
    "summary": "ONE PLACE, ONE MEMORY",
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RenderError(f"缺少文件：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_media_manifest(
    manifest: dict[str, Any], topic_id: str, minimum_width: int, minimum_height: int,
    minimum_assets: int = 1,
) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != "1.0" or manifest.get("topic_id") != topic_id:
        raise RenderError("媒体清单版本或 topic_id 与内容包不一致")
    assets = manifest.get("assets", [])
    if not assets:
        raise RenderError("媒体清单没有可用素材")
    for asset in assets:
        if asset.get("location_match_status") != "verified":
            raise RenderError(f"素材 {asset.get('asset_id')} 尚未通过地点一致性核验")
        path = Path(asset["local_path"])
        if not path.is_file():
            raise RenderError(f"素材文件不存在：{path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != asset.get("sha256"):
            raise RenderError(f"素材 {asset.get('asset_id')} 哈希不一致")
        with Image.open(path) as image:
            width, height = image.size
        if width != asset.get("width") or height != asset.get("height"):
            raise RenderError(f"素材 {asset.get('asset_id')} 尺寸元数据不一致")
        if width < minimum_width or height < minimum_height:
            raise RenderError(f"素材 {asset.get('asset_id')} 分辨率不足")
        if not manifest.get("fixture_mode"):
            required = ["source_page", "file_url", "creator", "license", "license_url", "credit"]
            if any(not asset.get(field) for field in required):
                raise RenderError(f"素材 {asset.get('asset_id')} 授权字段不完整")
    if not manifest.get("fixture_mode") and len(assets) < minimum_assets:
        raise RenderError(f"正式渲染至少需要 {minimum_assets} 张不同的已核验图片")
    return assets


def _write_sources(manifest: dict[str, Any], map_metadata: dict[str, Any]) -> str:
    lines = ["# 图片与地图来源", ""]
    if manifest.get("fixture_mode"):
        lines.extend(["> **TEST FIXTURE ONLY：合成测试图，不是真实地点照片，禁止发布。**", ""])
    for asset in manifest["assets"]:
        lines.extend(
            [
                f"## {asset['asset_id']}", "",
                f"- 原始页面：<{asset['source_page']}>",
                f"- 文件链接：<{asset['file_url']}>",
                f"- 作者：{asset['creator']}",
                f"- 许可证：{asset['license']}（<{asset['license_url']}>）",
                f"- 署名：{asset['credit']}",
                f"- 地点一致性：{asset['location_match_status']}；{'；'.join(asset['location_match_evidence'])}",
                f"- 访问时间：{asset['accessed_at']}",
                f"- SHA-256：`{asset['sha256']}`",
                f"- 原始尺寸：{asset['width']}×{asset['height']}；裁切：{asset['crop_policy']}",
                "- 衍生发布义务：如许可证包含 ShareAlike，发布卡片时须采用相同或兼容许可证。",
                "",
            ]
        )
        if asset.get("used_on"):
            lines.insert(-1, f"- 使用卡片：{'、'.join(f'第{number}张' for number in asset['used_on'])}")
    lines.extend(
        [
            "## 世界定位图", "",
            f"- 数据：{map_metadata['dataset']} {map_metadata['version']}（{map_metadata['scale']}）",
            f"- 原始链接：<{map_metadata['source_url']}>",
            f"- 数据哈希：`{map_metadata['source_sha256']}`",
            f"- 投影：{map_metadata['projection']}",
            f"- 坐标：{map_metadata['latitude']}, {map_metadata['longitude']}",
            f"- 边界说明：{map_metadata['boundary_note']}",
            f"- 署名：{map_metadata['credit']}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _manifest(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _photo_assets_by_card_number(
    cards: list[dict[str, Any]], assets: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    photo_cards = [card for card in cards if card["role"] != "location"]
    return {
        card["number"]: assets[index % len(assets)]
        for index, card in enumerate(photo_cards)
    }


def render_cards(
    root: Path,
    research_directory: Path,
    content_directory: Path,
    media_manifest: dict[str, Any],
    destination: Path,
    map_settings: dict[str, Any],
    render_settings: dict[str, Any],
    media_settings: dict[str, Any],
    force: bool = False,
) -> tuple[Path, dict[str, Any]]:
    try:
        research, _, research_sha256 = load_audited_research(research_directory)
    except ContentValidationError as exc:
        raise RenderError(str(exc)) from exc
    content_path = content_directory / "content_package.json"
    content = _load_json(content_path)
    content_audit = _load_json(content_directory / "content_audit.json")
    if not content_audit.get("passed"):
        raise RenderError("内容包未通过审计")
    content_sha256 = hashlib.sha256(content_path.read_bytes()).hexdigest()
    if content_audit.get("content_package_sha256") != content_sha256:
        raise RenderError("内容包哈希与审计记录不一致，禁止渲染")
    if content_audit.get("research_package_sha256") != research_sha256:
        raise RenderError("内容审计引用的研究包哈希不一致，禁止渲染")
    if research["topic_id"] != content["topic_id"]:
        raise RenderError("研究包与内容包 topic_id 不一致")
    if research.get("fixture_mode") != content.get("fixture_mode"):
        raise RenderError("研究包与内容包 fixture_mode 不一致")
    if media_manifest.get("fixture_mode") != content.get("fixture_mode"):
        raise RenderError("媒体清单 fixture_mode 与内容包不一致")
    assets = validate_media_manifest(
        media_manifest,
        content["topic_id"],
        int(media_settings["minimum_width"]),
        int(media_settings["minimum_height"]),
        int(media_settings["minimum_assets"]),
    )
    if destination.exists() and not force:
        raise FileExistsError(f"输出目录已存在：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        image_dir = temporary / "images"
        asset_dir = temporary / "assets"
        html_dir = temporary / "html"
        image_dir.mkdir()
        asset_dir.mkdir()
        html_dir.mkdir()
        copied_assets: list[dict[str, Any]] = []
        for asset in assets:
            source = Path(asset["local_path"])
            target = asset_dir / source.name
            shutil.copy2(source, target)
            copied = dict(asset)
            copied["local_path"] = str(target.resolve())
            copied_assets.append(copied)
        photo_assets = _photo_assets_by_card_number(content["cards"], copied_assets)
        uses_by_asset: dict[int, list[int]] = {id(asset): [] for asset in copied_assets}
        for card_number, asset in photo_assets.items():
            uses_by_asset[id(asset)].append(card_number)
        render_manifest = dict(media_manifest)
        render_manifest["assets"] = [
            {
                **asset,
                "used_on": uses_by_asset[id(asset)],
                "local_path": str(
                    destination.resolve() / "assets" / Path(asset["local_path"]).name
                ),
            }
            for asset in copied_assets
        ]
        (asset_dir / "media_manifest.json").write_text(
            json.dumps(render_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        shape_path, map_source = ensure_map_data(root, map_settings)
        map_svg = asset_dir / "locator-map.svg"
        map_metadata_path = asset_dir / "map_metadata.json"
        map_metadata = generate_locator_map(
            shape_path,
            map_source,
            map_svg,
            map_metadata_path,
            research["location"],
        )

        environment = Environment(
            loader=FileSystemLoader(root / "templates"),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = environment.get_template("card.html")
        css = (root / "templates" / "card.css").read_text(encoding="utf-8")
        width, height = int(render_settings["width"]), int(render_settings["height"])
        overflow: dict[str, list[str]] = {}
        font_checks: dict[str, bool] = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=str(render_settings["browser_channel"]), headless=True
            )
            context = browser.new_context(
                viewport={"width": width, "height": height},
                screen={"width": width, "height": height},
                device_scale_factor=1,
            )
            page = context.new_page()
            for card in content["cards"]:
                asset = photo_assets.get(card["number"], copied_assets[0])
                html_text = template.render(
                    css=css,
                    card=card,
                    card_total=len(content["cards"]),
                    title=content["title_options"][0]["text"],
                    location=research["location"],
                    role_label=ROLE_LABELS.get(card["role"], "DAILY WORLD"),
                    photo_uri=Path(asset["local_path"]).as_uri(),
                    map_uri=map_svg.resolve().as_uri(),
                    map_version=map_metadata["version"],
                    asset_credit=asset["credit"],
                    fixture_mode=content["fixture_mode"],
                )
                html_path = html_dir / f"{card['number']:02d}_{card['role']}.html"
                html_path.write_text(html_text, encoding="utf-8")
                page.goto(html_path.resolve().as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                font_ok = page.evaluate(
                    "font => document.fonts.check('24px ' + JSON.stringify(font))",
                    str(render_settings["font_family"]),
                )
                font_checks[str(card["number"])] = bool(font_ok)
                issues = page.evaluate(
                    """() => {
                      const nodes = [document.documentElement, document.body, ...document.querySelectorAll('[data-fit]')];
                      return nodes.filter(n => n.scrollWidth > n.clientWidth + 1 || n.scrollHeight > n.clientHeight + 1)
                        .map(n => n.tagName + (n.className ? '.' + String(n.className).replace(/ /g,'.') : ''));
                    }"""
                )
                overflow[str(card["number"])] = issues
                filename = FILENAMES[card["role"]]
                page.screenshot(path=str(image_dir / filename), type="png")
            context.close()
            browser.close()

        failures: list[str] = []
        if not all(font_checks.values()):
            failures.append("指定中文字体未加载")
        for number, issues in overflow.items():
            if issues:
                failures.append(f"卡片 {number} 文本或页面溢出：{', '.join(issues)}")
        image_checks: dict[str, dict[str, Any]] = {}
        for path in sorted(image_dir.glob("*.png")):
            with Image.open(path) as image:
                image_checks[path.name] = {
                    "width": image.width,
                    "height": image.height,
                    "format": image.format,
                    "bytes": path.stat().st_size,
                }
            check = image_checks[path.name]
            if (check["width"], check["height"], check["format"]) != (width, height, "PNG"):
                failures.append(f"{path.name} 尺寸或格式错误")
            if check["bytes"] < int(render_settings["minimum_png_bytes"]):
                failures.append(f"{path.name} 文件过小，可能为空白或低质量")
        if len(image_checks) != len(content["cards"]):
            failures.append("导出的 PNG 数量与卡片数量不一致")

        audit = {
            "audit_version": "1.0",
            "passed": not failures,
            "critical_failures": failures,
            "font_checks": font_checks,
            "overflow_checks": overflow,
            "image_checks": image_checks,
            "map_coordinate_match": {
                "latitude": map_metadata["latitude"] == float(research["location"]["latitude"]),
                "longitude": map_metadata["longitude"] == float(research["location"]["longitude"]),
            },
        }
        (temporary / "image_sources.md").write_text(
            _write_sources(render_manifest, map_metadata), encoding="utf-8"
        )
        (temporary / "render_audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        checklist = [
            "# 人工复核清单", "",
            "- [ ] 图片确实对应目标地点，未被裁切造成误导。",
            "- [ ] 照片能直接呈现本卡讲述的建筑或景观；需要总览的建筑主题至少有一张主体清楚的全景/中远景，局部特写只用于对应细节，不能代替建筑整体。",
            "- [ ] 按最终 1080×1440 裁切检查：主体足够大且清楚，未被截断、遮挡或文字压住；元数据正确不等于视觉合格。",
            "- [ ] 作者、许可证、链接和署名方式适合本次发布。",
            "- [ ] 对 CC BY-SA 等素材确认衍生作品的相同方式共享义务。",
            "- [ ] 地图点位、国家/地区名称和边界表达经过目视检查。",
            "- [ ] 每张图文字清楚、无截断、无错误换行。",
            "- [ ] 发布前复核小红书最新原创、版权与 AI 标识规则。",
            "- [ ] 人工确认后再发布；本程序未登录或发布。",
        ]
        if content["fixture_mode"]:
            checklist.insert(2, "> **TEST FIXTURE ONLY：本包禁止发布。**")
        (temporary / "review_checklist.md").write_text(
            "\n".join(checklist) + "\n", encoding="utf-8"
        )
        (temporary / "manifest.json").write_text(
            json.dumps({"files": _manifest(temporary)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if failures:
            raise RenderError("；".join(failures))
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        return destination, audit
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
