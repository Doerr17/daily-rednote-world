# 世界来信：系统设计与实现说明

## 1. 项目目标

本项目每天生产一个“待人工审核”的小红书地点内容包：从未使用地点中选题，实时研究并核对事实，取得可追溯授权的真实图片，按真实坐标制作地图，写作并排版 5～6 张 1080×1440 PNG，同时把研究成果沉淀为可检索的地理与人文知识档案。

项目不登录小红书、不自动发布、不制造互动或运营数据。只有人工审核通过后，才允许用显式命令登记为已发布。

## 2. 成功标准

一次 `python main.py daily` 只有同时满足以下条件才算成功：

1. 选中的地点未被使用，且与近期地点在国家/区域、地理类型、视觉主题和叙事角度上不过度相似。
2. 地名、国家、行政区、坐标和地图经过至少两个相互独立的数据来源核对。
3. 每条重要陈述都指向证据记录；易变信息包含“截至日期”。
4. 历史事实、研究者解释、民间传说、旅游宣传和未知项被分开标记。
5. 每张外部图片都有原始页面、作者、许可证、许可证链接、下载日期、地点一致性依据和文件哈希；授权不清即停用。
6. 地图由真实坐标和真实边界数据生成，包含数据来源与地图署名。
7. 生成 3～5 个标题、正文、标签、学习笔记、来源表、地点结构化数据和 5～6 张清晰卡片。
8. 自动检查尺寸、文件完整性、图片清晰度、文本溢出、禁用表达、近似重复与证据覆盖率。
9. 任一硬门禁失败时写入 `output/_failed/` 和日志，不产生可发布状态。

## 3. 非目标

- 不批量预生成几十篇成稿。
- 不把搜索摘要、模型记忆或旅游营销文案当成证据。
- 不把 AI 风景图、错位照片或无授权图片当成真实地点照片。
- 不自动登录或发布；真实发布后由人工通过 `python main.py publish DATE` 显式登记。
- 不根据虚构的阅读、收藏、评论或关注数据优化。

## 4. 核心工作流

```text
候选地点库 + 使用历史 + 最近内容指纹
                ↓ 选题评分与多样性约束
             当日地点/角度
                ↓ 实时检索与证据摘录
       研究账本 + 来源账本 + 未知项
                ↓ 独立事实复核
       文案草稿 + 卡片结构 + 学习档案
                ↓ 授权图片获取 + 真实地图生成
          HTML/CSS 版式 + Playwright 导出
                ↓ 自动 QA + 人工审核清单
              待审核内容包
```

所有阶段都通过结构化中间文件交接。下游只能使用已进入研究账本的事实，不能让模型临时补写未被引用的细节。

图片卡片保留短句化的信息层级；`content.md` 集中放置标题候选、完整发布正文和标签。正文必须在研究事实范围内重新组织叙事，不能把卡片文字逐段拼接；不再额外生成重复的 `title_options.md` 或 `caption.md`。

## 5. 技术路线

### 5.1 基础栈

- Python 3.10+：CLI、状态机、SQLite、检索适配器、校验和归档。
- SQLite：候选地点、别名、主题标签、运行记录、使用/发布历史、内容指纹、来源和运营数据。
- JSON Schema：约束模型输出和各阶段中间产物。
- HTML/CSS：版面模板；Playwright + Chromium：固定视口截图为 1080×1440 PNG。
- Pillow：图片尺寸、裁切、清晰度和 PNG 校验。
- pypdf：将可检索 PDF 转为可核对的缓存正文；扫描型 PDF 仍会被硬门禁拒绝。
- Jinja2：将结构化内容渲染进模板。
- 地图：经核验的地点坐标 + Natural Earth 行政边界；使用 pyshp 读取边界并生成 SVG 定位图，不让生成式模型绘制地图。

Python 依赖及版本约束列在 pyproject.toml；Playwright 浏览器另行安装或使用本机已安装的 Edge。

### 5.2 模型调用

真实研究与写作通过本机已安装并完成认证的 Codex CLI 执行非交互式调用，并使用 JSON Schema 约束结构化输出。运行者需自行安装并登录 Codex CLI；认证信息保存在本机，不属于仓库配置。

调用采用有限超时和失败处理，不把认证文件或密钥写入仓库。Codex CLI 的安装与认证方式见[官方文档](https://developers.openai.com/codex/cli)。

降级策略：

1. Codex CLI 已安装且认证有效：运行研究整理、文案和独立复核。
2. 登录失效/限额：停止在相应阶段，保留已完成产物并给出恢复命令；不退化为无来源的模板文案。
3. 后续若改用付费 API：只在用户显式配置后启用，按单次进程注入密钥，并记录模型、时间和调用状态，不记录密钥。

对已经生成但尚未发布的研究草稿，人工确认来源后可显式设置 `WORLDNOTE_CACHED_RESEARCH` 走恢复路径；程序仍重新抓取允许的来源并运行同一事实门禁，默认不会读取缓存草稿。

### 5.3 检索与证据

来源优先级不是“一张固定白名单”，而是按具体主张选择最接近原始记录的来源：

1. UNESCO、政府/地方主管机构、遗产管理机构、博物馆、档案馆、统计或气象机构。
2. 学术论文、大学/研究机构、同行评议或权威专著。
3. 官方旅游与交通机构，用于交通、开放时间和季节性信息，并注明核对日期。
4. 可靠编辑型资料，仅用于发现线索和交叉核对；不替代关键主张的一手证据。

每条 `claim` 记录：原文支持片段或精确定位、规范化陈述、类型、来源 ID、访问日期、适用时间、限制、复核状态。动态网页或无法定位证据的页面不得支撑关键事实。

### 5.3.1 文案与知识库

- 写作只读取已通过审计且 SHA-256 与审计记录一致的 `research_package.json`；研究文件被改动后必须重新审计。
- 标题、正文段落、卡片标题与正文、学习笔记总览与条目分别保存 `claim_refs`，下游可反查原始来源。
- Codex 写作和独立编辑审计均禁用搜索并运行在只读、临时会话；模型不能在写作阶段引入新资料。
- 确定性门禁检查禁用表达、虚假到访、无证据数字、卡片职责/顺序、内部重复和近期内容重复；编辑审计负责识别无法靠字符串规则发现的事实扩大和营销腔。
- 正式通过后才记录标题、正文、卡片和结构/语气关键词指纹，并生成 `knowledge/places/<slug>.md` 与 `knowledge/index.json`。fixture 永不进入正式数据库或知识库。

### 5.4 图片策略

首选 Wikimedia Commons API，因为可取得原始文件页和结构化授权字段；其次是明确声明可复用授权的政府、博物馆、UNESCO 或地方机构素材。Openverse 可作发现入口，但仍须回到原始托管页复核。Unsplash、Pexels、付费图库等只有在配置所需 Key、遵守各自条款并能完整归档授权时才启用。

每张图片保存：`original_url`、`source_page`、`creator`、`license`、`license_url`、`required_attribution`、`accessed_at`、`location_match_evidence`、`sha256`、原始尺寸、裁切参数和衍生文件。仅凭文件名或搜索结果缩略图不能证明地点一致性。

无合格实拍时，只能使用自制真实数据地图、信息图，或明确标为“概念插画”的合法素材；概念插画不能表现成现场实景。

当前 Commons 适配器使用 `imageinfo` 的 URL、尺寸、SHA-1、MIME 与 `extmetadata`，并读取页面分类和嵌入坐标。自动地点核验要求描述与精确地点分类同时命中，或地点文本命中且嵌入坐标距审计坐标不超过 25 km；仅文件名命中不会通过。正式渲染要求至少三张不同图片，缺作者、许可证、许可链接、署名或地点证据时停止。所有下载文件重新计算 SHA-256，保留原文件并只用 CSS `object-fit: cover` 进行非破坏性裁切。

### 5.5 地图策略

- 坐标主记录来自 Wikidata `P625` 或目标地点主管机构，并用第二来源复核。
- 世界定位图使用 Natural Earth 行政边界；地点以经纬度投影后标注。
- 需要详细道路/交通时再调用 OpenStreetMap 生态数据，并遵守服务使用政策、缓存与署名要求；不得高频抓取公共端点。
- 地图同时存源数据版本、坐标、投影、生成参数、署名和生成日期。
- 小岛、飞地或跨日界线地点必须使用插图/局部图处理并进行人工目视核对。

当前实现使用 Natural Earth 1:110m Admin 0 Countries 5.1.1 制作世界定位图。程序保存原始压缩包、下载链接和 SHA-256，以等距圆柱投影将审计坐标换算为 SVG 像素，并记录默认 de facto 边界视角。1:110m 数据适合世界定位，不用于街道、遗产边界或精细导航；小岛和争议边界仍保留人工目视复核项。

### 5.6 排版与视觉 QA

- Jinja2 将结构化卡片写入单卡 HTML，CSS 提供统一杂志式网格、字体、色彩与照片裁切。
- Playwright 按 config/project.json 指定的浏览器通道，以 1080×1440、device scale factor 1 截图；默认通道为 Microsoft Edge。
- 每张卡检查指定中文字体、页面及正文容器滚动尺寸、PNG 格式、像素尺寸、文件体积和总张数。
- 输出 `image_sources.md`、`render_audit.json`、`review_checklist.md`、地图元数据与逐文件 SHA-256 manifest；自动 QA 通过仍不代表人工版权/地点目视复核通过。

## 6. 数据模型

SQLite 中的表按用途分组：

- 选题：`locations`、`location_aliases`、`topic_candidates`、`topic_tags`、`candidate_sources` 和 `selections`。
- 研究：`research_runs`、`research_sources`、`research_claims`、`research_claim_sources` 和 `coordinate_evidence`。
- 文案：`content_runs` 和 `content_fingerprints`。
- 每日运行与运营：`daily_runs`、`daily_checkpoints`、`publications` 和 `metrics`。
- 图片、地图及逐项来源信息归档在每日输出的 manifest 与来源文件中，不单独作为 SQLite 表维护。

Markdown 是供人审核和学习的主界面；SQLite/JSON 是自动化的权威状态。两者由同一结构化对象生成，避免双写分叉。

## 7. 仓库与运行目录

仓库提交的是可复用代码、配置、种子和基础地图数据。数据库、每日内容、缓存、知识库正文和日志属于运行数据，默认留在本机并由 .gitignore 排除。

```text
.
├── PROJECT.md
├── TASKS.md
├── AGENTS.md
├── README.md
├── main.py
├── pyproject.toml
├── config/project.json
├── data/
│   ├── migrations/
│   ├── seeds/locations.json
│   └── geographic/natural-earth-5.1.1/
├── prompts/
├── schemas/
├── scripts/
├── src/worldnote/
├── templates/
├── tests/
├── knowledge/
│   └── places/.gitkeep
└── output/                 # 首次运行时创建，不提交
```

单日正式输出：

```text
YYYY-MM-DD/
├── research.md
├── learning_notes.md
├── sources.md
├── content.md
├── location.json
├── images/
│   ├── 01_cover.png
│   ├── 02_location.png
│   ├── 03_landscape.png
│   ├── 04_culture.png
│   ├── 05_facts.png
│   └── 06_summary.png       # 五卡结构时可不生成
├── assets/                  # 原图、授权与地图中间文件
├── audit.json
├── manifest.json
└── review_checklist.md
```

## 8. 状态、失败恢复与幂等性

运行状态依次为：`selected → researched → fact_checked → drafted → assets_ready → rendered → qa_passed → awaiting_review`。`failed` 保存失败阶段与可恢复上下文；`published` 只能人工登记。

- 每个阶段先写临时工作目录，成功后原子替换该阶段产物。
- 每个文件写 SHA-256 到 `manifest.json`。
- 相同日期重复运行默认从最后一个已验证检查点继续；`--restart-stage` 只重做指定阶段及其下游。
- 正式日期目录只在全部自动门禁通过后落成；失败稿进入 `_failed/<run_id>/`。
- 日志不得包含认证信息、完整提示中的秘密或不必要的个人数据。

正式运行在 `daily_runs` 和 `daily_checkpoints` 中记录状态，工作文件位于 `output/_runs/<date>-<run-id>/`，事件写入 `logs/daily-<run-id>.jsonl`。失败不会删除已完成阶段；同日再次运行复用原 run 和选题。`--restart-stage` 只重做指定阶段及下游，且只允许作用于已验证的运行目录。最终 `output/YYYY-MM-DD/` 由临时目录原子提交，已有目录不会静默覆盖。

`published` 不是自动流水线状态：只有内容已到 `awaiting_review`，且人工真实发布后显式运行带 `--confirm-reviewed` 的 `publish` 命令才会写入。运营数据同样只接受人工快照；项目不推断、不抓取、不补零，也不根据虚构数据优化。

## 9. 质量门禁

硬门禁包括：坐标冲突、关键事实无证据、来源打不开、许可证不明、图片地点无法确认、卡片尺寸错误、文本溢出、文件缺失、与近期内容过度重复。命中任何一项即失败。

软警告包括：来源类型单一、季节/交通信息接近过期、图片构图单一、句式重复、标签过泛。软警告进入人工检查清单，不能被模型静默忽略。

自动化不能判断的项目明确留给人工：照片是否真的呈现目标地点、文化表述是否冒犯或简化、地图标签是否误导、整体叙事是否自然、平台当期规则和 AI 标识要求。

## 10. 运行依赖与平台边界

- Python 3.10 或更高版本；Python 包版本约束见 pyproject.toml。
- 真实研究和写作需要本机已安装并完成认证的 Codex CLI，以及可访问来源网站的网络连接。
- 图片搜索和资料网站可能限流、变更或暂时不可用；程序会保留检查点，但不能保证外部服务始终可用。
- 地图仅为世界范围定位图；Natural Earth 1:110m 边界不适合街道导航或精细遗产边界。
- 渲染器默认使用 Microsoft Edge 通道和 Microsoft YaHei 字体。其他平台可以调整 config/project.json 的浏览器通道和字体设置；使用 Playwright Chromium 时需另行安装浏览器。
- 每日流程只生成待审核素材，不创建定时任务、不登录或发布到小红书。任何实际发布和运营数据均由人工确认与登记。
