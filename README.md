# 世界来信

“世界来信”是一套小红书旅行地理图文的本地生产工具。它从地点候选库中选题，检索并核验资料，整理真实图片及其授权信息，按真实坐标制作定位图，生成中文正文、学习笔记和 5～6 张竖版卡片。自动流程只生成待审核素材，不登录或发布到小红书；发布和运营数据登记由人手动完成。

## 功能

- 按国家、地区、洲、地点类型、主题及近期选题记录平衡选题，避免重复。
- 研究来源和事实逐项留痕；来源冲突、证据不足或坐标未经核验时停止生成。
- 检索 Wikimedia Commons 图片并记录来源、作者、许可和地点证据；自动检查不代替人工审图。
- 使用真实坐标和仓库内的 Natural Earth 边界数据生成定位图。
- 将标题候选、完整正文和标签写入 `content.md`，并生成学习档案及可检索的本地知识库。
- 用 HTML/CSS 与 Playwright 导出 1080×1440 PNG，并检查尺寸、字体和文字溢出。
- 记录运行检查点，失败后可从最后完成的阶段恢复。

## 快速开始

需要 Python 3.10 或更高版本、网络连接，以及用于真实研究和写作的 Codex CLI。安装和登录方法见 [Codex CLI 官方文档](https://developers.openai.com/codex/cli)。本项目通过已安装的 CLI 发起研究和写作调用；认证信息保存在本机，不要提交到仓库。

克隆仓库并安装 Python 依赖：

~~~powershell
git clone https://github.com/Doerr17/daily-rednote-world.git
cd daily-rednote-world
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux 使用：source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
~~~

渲染器默认使用 Microsoft Edge（`config/project.json` 中的 `render.browser_channel`）和 Microsoft YaHei 字体。运行前请确认本机有对应浏览器和中文字体。也可以把浏览器通道改为 `chromium`，并确保 `render.font_family` 指向本机可用的中文字体，再安装 Playwright Chromium：

~~~powershell
python -m playwright install chromium
~~~

然后运行环境检查：

~~~powershell
python main.py doctor
~~~

日常完整流程：

~~~powershell
python main.py daily
~~~

流程需要联网访问资料与图片来源，并调用已登录的 Codex CLI；成功后素材位于 `output/YYYY-MM-DD/`，状态为 `awaiting_review`。运行结束后仍需人工检查事实、图片地点和版权、地图、文字与平台规范，再手动发布。正常流程由使用者手动启动，不会创建计划任务。

仓库中另有 Windows 任务注册辅助脚本 `scripts/register-daily-task.cmd`。正常流程不会调用它；该脚本默认只预览，只有显式传入 `REGISTER` 才会修改系统任务计划程序。

不调用模型的离线流程可用于验证内容管线：

~~~powershell
python main.py daily --provider fixture
~~~

Fixture 输出带有测试标识，只用于测试，不能发布。浏览器渲染依赖仍须满足。

## 常用命令

~~~powershell
python main.py --help
python main.py topics
python main.py history
python main.py select                 # 仅预览，不写选题历史
python main.py knowledge --json
python main.py map-data               # 检查或准备地图数据
python -m unittest discover -s tests -t . -v
~~~

要单独运行研究、文案、选图或渲染阶段，可先查看相应命令的帮助，例如 `python main.py research --help`。`select` 默认是 dry-run；只有显式提供 `--commit` 才会将选题写入本地历史。更多工作流和恢复说明见 [PROJECT.md](PROJECT.md)。

## 输出与本地数据

~~~text
output/YYYY-MM-DD/
├── research.md
├── learning_notes.md
├── sources.md
├── content.md
├── location.json
├── images/
├── assets/
├── audit.json
├── manifest.json
└── review_checklist.md
~~~

研究、正文、来源账本、图片授权信息和审计记录会随素材包归档。实际图片数量按地点调整，默认最多六张卡片。

GitHub 仓库包含程序、配置、提示词、测试、初始地点种子和 Natural Earth 地图数据。运行产生的 SQLite 数据库、选题历史、后续知识档案、缓存、日志和每日素材都由 `.gitignore` 排除，仅保存在运行机器上。新克隆的副本会从随仓库提供的种子开始，既不会带入原运行者的历史，也不会上传或覆盖其本地生成内容。

## 内容与授权边界

- 不自动登录、发布、刷量或模拟真人互动；`publish` 命令只在人工真实发布后登记状态。
- 不用搜索摘要替代来源，不把未经核验的地点、照片或生成式地图当作真实资料。
- 自动版权和地点检查不是法律判断，也不代替人工确认图片授权、署名和构图。
- 项目本身暂未附许可证。公开可见不等于获准复制、修改、分发或商用；仓库中的第三方数据和素材按各自来源条款处理。

## 文档

- [PROJECT.md](PROJECT.md)：系统架构、研究规则、状态与恢复机制。
- [TASKS.md](TASKS.md)：已实现范围与验证方式。
- [AGENTS.md](AGENTS.md)：内容质量与工程协作规范。
