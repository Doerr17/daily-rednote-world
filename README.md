# 世界来信

一个“来源先行、人工终审”的小红书旅行地理内容生产项目。目标命令是：

```powershell
python main.py daily
```

它最终会自动选择一个未介绍过且具有明确知识价值的地点，完成实时研究、事实复核、合法图片获取、真实地图制作、自然中文写作、1080×1440 卡片排版、质量审计和知识库归档。它只生成待审核素材包，不会登录或发布到小红书。

## 当前进度

阶段 0～6 已完成；现已把标题候选并入 `content.md`，移除重复的 `caption.md`，并保留 9 月 22、23 日两份正式待审包。每日运行由人工发起，最终状态停在 `awaiting_review`，不会自动登录或发布。

详细设计见 [PROJECT.md](PROJECT.md)，分阶段任务与验收标准见 [TASKS.md](TASKS.md)，内容与工程硬约束见 [AGENTS.md](AGENTS.md)。

## 项目目录与日常运行

本项目独立放在 `daily-world-rednote/` 目录中；选题历史、知识库、研究缓存和每日输出都以该目录为根，不与其他项目共用数据。

公开仓库保存程序、配置、文档、测试，以及运行必需的初始地点种子库和 Natural Earth 地图边界数据。数据库、后续积累的个人知识库、缓存、日志和每日生成的内容包由 `.gitignore` 排除，只保存在本机；因此，新克隆后可以复用基础地点与地图数据，运行过程中生成的内容仍留在本机。当前工作目录中的文件不会因 Git 忽略规则而删除。

每天由人工在项目目录运行 `python main.py daily`。本项目不创建或注册每日定时任务，也不会自动登录或发布。

## 当前可用命令

```powershell
python main.py doctor
python main.py topics
python main.py topics --all --json
python main.py history
python main.py select --date 2026-09-21 --seed demo
python main.py knowledge --json
python main.py map-data
python main.py daily
```

`select` 默认是 dry-run，不写入历史。只有人工明确执行下面的命令，才会占用该日期和地点：

```powershell
python main.py select --date 2026-09-21 --seed demo --commit
```

选题库当前包含 12 个跨洲地点和对应的 UNESCO 起始来源。来源只用于证明候选地点身份和选题角度；它们不是完整研究。地点坐标初始标记为 `pending_research`，只有在研究运行中经独立来源复核后才能变为 `verified` 并供地图使用。

离线 fixture 研究验收：

```powershell
python main.py research --provider fixture --date 2026-09-21 --force
```

fixture 会输出到 `output/_fixtures/research/`，所有文件都带有 `TEST FIXTURE ONLY` 标记，并且程序禁止把它写入正式研究数据库。

真实研究入口已经接通，但会使用本机 Codex 登录和实时网络：

```powershell
python main.py research --provider codex --topic lalibela-hewn-from-rock --date 2026-09-21
```

研究代理只能提交结构化草稿。程序随后会重新抓取来源、保存正文与哈希、核对证据摘录、检查两个独立坐标来源，再启动一个没有搜索权限的独立审计会话。任何 unsupported、conflicted、缺失摘录、坐标冲突或易变事实缺少日期都会停止运行。正式研究只有全部通过后才写入 SQLite。

离线 fixture 文案验收（需先运行上面的 fixture 研究命令）：

```powershell
python main.py write --provider fixture --force
```

输出位于 `output/_fixtures/content/`，包含标题、正文、卡片 JSON、学习笔记、知识档案预览、结构化内容包和审计。所有文件均标为 `TEST FIXTURE ONLY`；程序禁止将其写入正式内容表或 `knowledge/`。

真实写作只接受已经通过审计且哈希未变化的研究目录：

```powershell
python main.py write --provider codex --research output/_research/2026-09-21-lalibela-hewn-from-rock
```

写作与独立编辑均使用无搜索、只读、临时 Codex 会话。标题、正文段落、卡片标题/正文和学习笔记都必须引用研究包中的 `claim_id`；未知 claim、无证据数字、禁用套话、虚假到访、卡片重复或审计 error 会停止运行。通过后才写入正式内容指纹和 `knowledge/places/<slug>.md`。

正式知识库支持组合筛选：

```powershell
python main.py knowledge --country 埃塞俄比亚 --continent 非洲 --theme 岩石建筑
```

图片候选先从已经通过审计的研究目录检索：

```powershell
python main.py media-search --research output/_research/2026-09-21-lalibela-hewn-from-rock
```

候选 JSON 会记录 Commons 原始页面、作者、许可证、许可链接、署名、分辨率、描述、分类、坐标和地点一致性证据。只有授权完整并满足地点核验规则的候选可以下载；仅命中文件名不算证据。

选图还必须有代表性、可读性：照片要直接呈现对应卡片讲述的建筑或景观；需要总览的建筑主题至少安排一张主体清楚的全景/中远景，门窗或墙面特写只用于说明对应细节，不能代替建筑整体。按最终 1080×1440 裁切检查主体是否足够大、清晰且未被截断、遮挡或文字压住。地点和授权核验通过不代表构图过关，这项需要人工目视检查；不合格时换图或调整版式。

```powershell
python main.py media-download --candidates output/_research/2026-09-21-lalibela-hewn-from-rock/media_candidates.json --index 1 --output output/_media/lalibela
```

重复执行可向同一 `media_manifest.json` 追加不同图片。正式渲染至少需要 3 张已核验高清图：

```powershell
python main.py render `
  --research output/_research/2026-09-21-lalibela-hewn-from-rock `
  --content output/_content/2026-09-21-lalibela-hewn-from-rock `
  --media-manifest output/_media/lalibela/media_manifest.json
```

离线视觉验收：

```powershell
python main.py render --provider fixture --force
```

结果位于 `output/_fixtures/rendered/`。其中测试背景是程序生成的抽象色块，并带有“TEST FIXTURE ONLY · 禁止发布”标识；它只用于验证排版，不代表真实景观。

## 每日自动化与恢复

正式每日运行：

```powershell
python main.py daily
```

流程完成后停在 `awaiting_review`，不会登录或发布。相同日期再次执行会从数据库检查点恢复；需要主动重做某阶段时：

```powershell
python main.py daily --restart-stage assets_ready
```

允许重做的范围是 `researched`～`qa_passed`；未发布的 `awaiting_review` 包也可显式重做，已发布运行禁止重做。若实时研究因来源限流或模型调用不可用，可在人工确认缓存稿来源后显式设置 `WORLDNOTE_CACHED_RESEARCH=<path>` 恢复，程序仍会重新抓取来源并运行事实审计，不会默认启用该降级路径。离线完整验收命令：

`content.md` 集中放置 3～5 个标题候选、完整连贯正文和标签；它与图片内嵌文字分开创作，不把卡片短句机械拼接成正文。研究、学习笔记、来源和审计文件仍独立保留，方便复核。

```powershell
python main.py daily --provider fixture --date 2026-09-22
```

fixture 最终包位于 `output/_fixtures/daily/final/`，不会写入正式数据库或知识库。

真实发布完成后，人工显式登记：

```powershell
python main.py publish 2026-09-22 --confirm-reviewed --url "真实笔记链接"
```

未提供 `--confirm-reviewed`、运行未到 `awaiting_review` 或同日已经登记发布时都会失败。真实运营数据只能在登记发布后录入：

```powershell
python main.py metrics add 2026-09-22 --as-of 2026-09-23T20:00:00+08:00 `
  --views 0 --likes 0 --saves 0 --comments 0 --followers-gained 0
python main.py metrics list --date 2026-09-22
```

示例中的 0 只是命令格式，实际使用时必须录入平台显示的真实数据。

本项目不注册 Windows/Codex 定时任务。`scripts/register-daily-task.cmd` 是旧的可选辅助脚本，不属于当前工作流；每天由用户手动提交任务并运行 `python main.py daily`。

测试：

```powershell
python -m unittest discover -s tests -t . -v
```

## 每日内容包

```text
output/YYYY-MM-DD/
├── research.md
├── learning_notes.md
├── sources.md
├── content.md              # 标题候选、连贯正文和标签
├── location.json
├── review_checklist.md
├── images/
│   ├── 01_cover.png
│   ├── 02_location.png
│   ├── 03_landscape.png
│   ├── 04_culture.png
│   ├── 05_facts.png
│   └── 06_summary.png
├── assets/
├── audit.json
├── manifest.json
└── review_checklist.md
```

每篇还保留图片来源、授权信息、结构化研究资料、审计记录和文件哈希 manifest；`output/_runs/`、`output/_failed/` 与 `output/_fixtures/` 只作本机恢复或测试，不进入 Git。

## 已验证的本机条件

- Python 3.10.5、Node 24.19.0、npm 11.17.0 可用。
- Codex CLI 0.153.4 可用，当前为 ChatGPT 登录；PowerShell 执行策略会拦截 `codex.ps1`，后续使用 `codex.cmd`。
- Chrome 和 Edge 已安装；Playwright 项目依赖和浏览器缓存尚未安装。
- Noto Sans SC、微软雅黑、思源宋体等中文字体可用。
- 2026-09-21 实测 Wikidata、OpenStreetMap Nominatim、Wikimedia Commons API 端点均返回 HTTP 200。
- 未检测到 OpenAI、Mapbox、Unsplash、Pexels 等环境变量 Key。
- Pillow、Jinja2、Playwright Python 包和 GIS 包尚未安装；将在对应阶段安装并锁定。

因此，联网检索、真实图片元数据、真实坐标地图和浏览器排版在本机具备可行路径，但仍需在后续阶段逐项实现、限速并测试。首版不会依赖付费 Key；没有明确授权的图片不会进入内容包。

## 阶段确认

下一步需人工确认后进入阶段 6“真实地点端到端验收”：由选题系统选择真实地点，完整执行研究、图片、地图、写作、排版和人工审查，不为了演示成功而降低任何门禁。
