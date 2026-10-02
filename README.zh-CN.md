# Job Extractor

[English](README.md) | **中文文档**

输入招聘网站 URL，自动提取岗位列表和完整 JD，并生成可直接交给 GPT 等 AI 使用的结构化文件，用于岗位推荐、筛选和分析。

Extract job listings and full job descriptions from recruitment websites into structured files for AI-assisted job recommendation, filtering and analysis.

## 为什么需要 Job Extractor？

不同公司的招聘官网结构差异很大。当一个公司发布几十甚至上百个岗位时，逐个打开页面阅读 JD 非常低效。

Job Extractor 把招聘页面转换为干净的结构化数据，让你（或 AI 助手）一次性处理全部岗位：

```
招聘网站
   ↓
Job Extractor
   ↓
结构化岗位数据   (jobs.json / jobs.csv / jobs.xlsx / report.md)
   ↓
  GPT / AI
   ↓
岗位推荐 / 筛选 / 分析
```

你不再需要逐个岗位自己看。提取一次，剩下的交给 AI。

## 功能特性

- **自动发现岗位列表** — 分析网站，找到背后的岗位列表 API，带分页采集全部岗位
- **完整 JD 提取** — 抓取完整岗位描述（职责 / 要求），而不只是职位名称
- **多平台支持** — 为多个招聘平台提供专用适配器，另有通用引擎处理未知网站
- **多范围采集** — 校招、社招等独立招聘范围分别采集后合并去重
- **公司/品牌感知输出** — 运行目录按识别出的公司或品牌命名
- **去重** — 跨页重复投递的岗位自动合并，并保留审计记录
- **浏览器兜底** — JavaScript 渲染的网站自动回退到无头浏览器
- **手动 cURL 兜底** — 受保护的网站接受从浏览器复制的 cURL 请求
- **多种导出格式** — 每次运行输出 JSON、CSV、XLSX 和 Markdown 报告

## AI 工作流

Job Extractor 负责提取和结构化，AI 负责推荐和分析：

1. 对招聘 URL 运行 Job Extractor
2. 打开运行目录，取 `jobs.json`（或 `jobs.csv` / `report.md`）
3. 上传文件到 ChatGPT 或其他 AI 助手
4. 提问，例如：

   > "根据我的背景和职业目标，给这些岗位排序并推荐最匹配的几个。"

## 已支持平台

已验证的招聘平台：

- **Moka** — Moka 驱动的招聘官网
- **飞书 / Lark** 招聘网站
- **Zhiye（智业）** — `*.zhiye.com` 招聘网站
- **北森 CMS（Beisen）** — 北森驱动的招聘官网

其他网站走**通用引擎**：自动发现岗位列表 API，必要时回退到无头浏览器。通用引擎的成功率取决于具体网站——请视为实验性能力而非保证。Job Extractor 不承诺支持所有招聘网站。

## 安装

> **需要 Python 3.10 或更高版本。** 先运行 `python3 --version` 确认。
> 如果系统 Python 低于 3.10，请先安装 Python 3.10+。

macOS / Linux：

```bash
git clone https://github.com/Finn-623/job_extractor.git
cd job_extractor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Windows（PowerShell）：

```powershell
git clone https://github.com/Finn-623/job_extractor.git
cd job_extractor
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## 快速开始

```bash
python main.py "https://example.com/jobs"
```

> `example.com` 仅为格式示意，不是已验证支持的真实站点。请使用真实招聘页面 URL。

常用选项：

```bash
python main.py "URL" --scope campus     # 消歧招聘范围：campus / social / intern / all
python main.py "URL" --discover         # 只观测并报告疑似岗位 API
python main.py --list-curl "<curl>" --detail-curl "<curl>"   # 手动 cURL 兜底
```

无需设置 `PYTHONPATH` 或其他环境变量——在仓库根目录直接运行即可。

> 如果某个网站自动抓取失败，程序会先用无头浏览器重试；仍拿不到数据时，可以从浏览器复制 cURL 请求交给程序处理——见[手动 cURL 兜底](#手动-curl-兜底)。

## 输出

每次成功运行会创建一个带时间戳的目录：

```
output/
└── 公司名称/
    └── 2026-01-01_120000/
        ├── jobs.json         # 机器可读，最适合 AI / 自动化流程
        ├── jobs.csv          # 扁平表格，适合筛选和数据分析
        ├── jobs.xlsx         # Excel 工作簿，适合人工查看
        ├── report.md         # 可读的运行摘要，可直接上传给 AI
        └── collection.json   # 采集过程元数据（耗时、指标、审计）
```

- **jobs.json** — 结构化岗位记录，含完整 JD 文本；AI 工作流的首选文件
- **jobs.csv** — 每行一个岗位，便于过滤和透视
- **jobs.xlsx** — 相同数据的 Excel 排版
- **report.md** — 运行概览：总数、完整性标记、逐岗位摘要
- **collection.json** — 数据是怎么采集的（模式、耗时、去重审计）

## 公司 / 品牌目录命名

运行目录通常会以招聘网站背后的公司或品牌命名，例如：

```
output/深圳市睿联技术有限公司/2026-01-01_120000/
output/Usmile/2026-01-01_120000/
```

系统会尽量从岗位数据、招聘站点信息或招聘域名中识别可用的公司/品牌名称——多数情况下能得到一个可用的名字。只有确实无法可靠识别时才使用 `_unknown/` 兜底，它应该是例外而不是常态。

## List-only 站点

某些招聘网站的岗位列表可以完整获取，但职位详情页存在较强的浏览器验证或访问限制。此时运行可能以如下状态结束：

- collection status = `COMPLETE`
- jd_strategy = `LIST_ONLY`

含义是：

- 岗位列表已经完整采集（岗位名称、地点、类别、发布时间等）
- 完整 JD 没有批量获取
- **这不是采集失败**——岗位列表本身就是有效结果
- 导出的岗位列表仍可用于初步筛选，或交给 ChatGPT/AI 排序和初筛
- 对感兴趣的岗位，再前往招聘官网查看完整 JD

Job Extractor 不会为了获取这些 JD 绕过任何验证或访问限制；`LIST_ONLY` 是对"实际能采集到什么"的如实记录。

## 失败 / 未知输出

- `output/_unknown/<时间戳>/` — 最终兜底：确实无法可靠识别公司/品牌名时使用
- `output/_failed/<公司名或 _unknown>/<时间戳>/` — 运行失败；目录内只有描述失败原因的 `error_report.json`

成功的运行不会写入这两个目录。

## 自动提取

给定招聘 URL，Job Extractor 会观察页面如何加载岗位列表，识别背后的列表 API，构建采集计划，并翻页抓取完整列表。随后抓取岗位详情，清洗为带完整性标记的结构化记录，让你准确知道每个 JD 抓到了多少内容。JD 内容从不伪造——只记录源站真实提供的字段。

## 浏览器兜底

有些网站只在浏览器里渲染岗位列表。当纯 HTTP 采集不够时，Job Extractor 会用无头 Chromium 重新加载页面并读取渲染结果。

## 手动 cURL 兜底

Job Extractor 按三层顺序尝试：

```
自动模式
      ↓  网站无法自动发现时
Manual cURL fallback（手动 cURL 兜底）
      ↓  cURL 能读到岗位、但翻页请求无法安全复用时
Browser-assisted collection（浏览器辅助采集）
```

手动 cURL 适用于：

- 网站自动抓取失败
- 网站通过前端 API / XHR 请求加载岗位

涉及两种 cURL：

- **List cURL**：岗位列表请求，返回职位名称、岗位 ID、分页信息等
- **Detail cURL**：单个岗位的详情请求，返回完整 JD / 岗位描述

从浏览器开发者工具把请求复制为 cURL，通过 `--list-curl` / `--detail-curl`（或 `--list-curl-file` / `--detail-curl-file`）交给程序继续采集。cURL 只被解析，绝不会被执行为 shell 命令。

详细教程：[docs/MANUAL_CURL.md](docs/MANUAL_CURL.md)

> **安全提醒**：浏览器复制出的 cURL 可能包含 Cookie、Token、`Authorization`、Session 信息。不要直接把原始 cURL 上传到 GitHub、Issue、日志或公开聊天中；使用前应先检查并删除敏感认证信息。

## 浏览器辅助采集

有些网站的翻页请求带有动态验证信息：cURL 能正常读取当前页，但用同一请求继续翻页会被网站拒绝（`CURL_PAGINATION_NOT_REPLAYABLE`）。此时 CLI 会提供浏览器辅助采集：

1. 像平常一样输入 URL；程序判断需要浏览器辅助时，CLI 会显示浏览器辅助选项
2. 选择后，CLI 会**把「自动翻页助手」复制到剪贴板**，并在**你的正常浏览器**中打开职位列表页
3. 在浏览器里打开开发者工具 → Network 录制，把助手粘贴到 Console 运行——它只点击网站自己的翻页按钮直到最后一页；网站弹出验证时会**自动暂停**
4. **网站验证由你本人在正常浏览器中完成。** 程序不会生成反爬 Token、不会绕过验证码、也不会接触你的 Cookie
5. 翻页完成后，在开发者工具里把 Network 记录**导出为 HAR 文件**
6. 回到 CLI 选择该 HAR 文件（或直接选"我已经有 HAR"）——程序**纯离线**解析导入：先失败后成功的页采用成功响应，跨页重复按稳定 ID 去重

详情页受强访问限制的站点可能以 List-only 结果结束——见 [List-only 站点](#list-only-站点)。

> **安全提醒**：导出的 HAR 可能包含 Cookie、Token、`Authorization`、Session 信息，分享前先检查并删除；Job Extractor 的输出文件不会包含这些值。

## 示例输出

在实际运行前，可以先查看[示例输出](examples/example_output/)，了解程序最终会生成哪些文件——完全虚构的数据（Example Robotics Ltd.），由同一套导出管线生成：`jobs.json`、`jobs.csv`、`jobs.xlsx`、`report.md`、`collection.json`。

## 文档

- [用户指南](docs/USER_GUIDE.md)
- [手动 cURL 指南](docs/MANUAL_CURL.md)
- [故障排查](docs/TROUBLESHOOTING.md)
- [更新日志](CHANGELOG.md)
- [参与贡献](CONTRIBUTING.md)

## 项目结构

```
job_extractor/     # 核心包
tests/             # 测试套件
scripts/           # benchmark 工具
docs/              # 文档
examples/          # 使用示例
output/            # 运行结果（运行时生成）
main.py            # CLI 入口
```

## 局限性

- 不保证支持所有招聘网站——网站结构各不相同且经常变化
- 页面改版可能导致提取失败，需等待工具更新
- 部分网站需要手动 cURL 兜底或浏览器辅助采集
- 受限站点可能以 `COMPLETE` + `LIST_ONLY` 结束——列表完整采集、JD 未批量获取（见 [List-only 站点](#list-only-站点)）
- 本项目**不会**绕过登录、验证码或其他访问控制
- 目标网站的频率限制和服务条款仍然适用

## 安全提醒

不要公开或提交 Cookie、Token、`Authorization` 头，或包含认证信息的 cURL 命令。参见 [SECURITY.md](SECURITY.md)。

## 免责声明

本项目用于对公开发布的招聘信息进行个人学习、研究和数据整理。你需自行确保遵守目标网站的服务条款、robots 政策、访问频率限制及适用法律。本项目不用于绕过登录、验证码或其他访问控制。

## 许可证

本项目基于 [MIT License](LICENSE) 发布。
