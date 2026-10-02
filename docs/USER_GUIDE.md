# Job Extractor 用户指南

本指南面向第一次使用本工具的用户。跟着做，你可以完成：

**安装 → 运行 → 输入招聘网站 URL → 等待抓取 → 找到结果文件 → 交给 ChatGPT 等 AI 做岗位推荐**

- [English README](../README.md) | [中文 README](../README.zh-CN.md)

## 1. 这个工具是干什么的

输入一个招聘网站的 URL，程序会自动：

1. 提取该公司发布的**全部岗位列表**
2. 抓取每个岗位的**完整 JD**（岗位职责、任职要求等）
3. 生成结构化文件：**JSON / CSV / XLSX / Markdown**

这些文件可以直接上传给 ChatGPT 等其他 AI，让 AI 帮你：

- 筛选岗位
- 推荐岗位
- 比较多个岗位
- 根据你的专业和背景排序

也就是说，**你不需要一个一个点开岗位页面自己读**——提取一次，剩下的交给 AI。

## 2. 开始前需要准备什么

| 需要 | 说明 |
|------|------|
| Mac 或 Windows 电脑 | — |
| Python 3.10 或更新版本 | 终端运行 `python3 --version`（Mac）或 `python --version`（Windows）确认 |
| Git | 用于下载本项目 |
| 网络连接 | 抓取时需要访问目标招聘网站 |
| 浏览器 | 用于后续手动 cURL 兜底（可选） |

Playwright / Chromium 是自动安装的依赖之一，用于处理需要浏览器渲染的网站，安装步骤见下文。

## 3. 下载项目

打开终端（Mac：Terminal；Windows：PowerShell），执行：

```bash
git clone https://github.com/Finn-623/job_extractor.git
cd job_extractor
```

## 4. 创建虚拟环境

**Mac / Linux：**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows（PowerShell）：**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> 如果 PowerShell 提示禁止运行脚本，可改用 `.venv\Scripts\activate.bat`（在 cmd 中）或执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 后重试。

看到终端提示符前面出现 `(.venv)` 即表示虚拟环境已激活。

## 5. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

> `playwright install chromium` 在 Mac 和 Windows 上命令相同，用于下载浏览器内核（处理 JavaScript 渲染的网站时需要）。

## 6. 第一次运行

```bash
python main.py "招聘网站URL"
```

- Mac 上如果 `python` 命令不存在，请使用 `python3 main.py "URL"`
- URL 需要用引号包起来
- `https://example.com/jobs` 仅为格式示意，请替换为真实招聘页面 URL
- 不需要设置 `PYTHONPATH` 或其他环境变量，在项目根目录直接运行即可

```bash
python main.py "https://example.com/jobs"
```

## 7. 运行过程中会发生什么

程序会在终端显示 5 个阶段的进度：

```text
[1/5] 识别招聘网站      ← 分析网站类型，找到岗位数据来源
[2/5] 获取岗位列表      ← 自动翻页，抓取全部岗位
[3/5] 处理岗位数据      ← 去重、整理、生成唯一岗位记录
[4/5] 获取完整 JD       ← 抓取岗位职责、任职要求等详情
[5/5] 保存结果          ← 生成 JSON / CSV / XLSX / Markdown 文件
```

运行时间取决于岗位数量，通常几十秒到几分钟。完成后终端会显示输出文件位置。

> 这 5 个阶段属于第一层「自动模式」。如果自动处理失败，程序还会依次尝试 Manual cURL fallback（手动 cURL 兜底）和 Browser-assisted collection（浏览器辅助采集），三层机制见第 12 节。

## 8. 去哪里看结果

所有结果保存在项目目录下的 `output/` 中。正常完成的运行类似：

```text
output/
└── 公司名称或品牌/          ← 按识别出的公司/品牌命名（如 深圳市睿联技术有限公司）
    └── 2026-01-01_120000/   ← 运行时间戳
        ├── jobs.json
        ├── jobs.csv
        ├── jobs.xlsx
        ├── report.md
        └── collection.json
```

`output/` 下还有两个以 `_` 开头的特殊目录（见第 13、14 节）：

```text
output/
├── _failed/
└── _unknown/
```

> 不想先跑真实网站？可以先看[示例输出](../examples/example_output/)——用虚构公司数据、由同一套导出管线生成的完整结果。

## 9. 每个文件是干什么的

| 文件 | 用途 |
|------|------|
| `jobs.json` | 最完整的结构化数据，含每个岗位的完整 JD 文本。**交给 AI / 程序处理时的首选文件** |
| `jobs.csv` | 扁平表格，一行一个岗位。适合用 Excel / WPS / Numbers 直接打开快速浏览 |
| `jobs.xlsx` | Excel 工作簿（含 Summary / Jobs / Data Quality 工作表），适合人工审阅 |
| `report.md` | 可读性最好的运行报告：总数、完整性标记、逐岗位摘要。**也适合直接上传给 ChatGPT** |
| `collection.json` | 本次采集过程的元数据（模式、耗时、去重审计）。普通用户一般不需要看 |

## 10. 怎么把结果交给 GPT

Job Extractor 只负责**提取和整理**岗位数据；岗位推荐、筛选、分析由 AI 完成。

**最简单的方法：**

1. 打开本次运行目录，找到 `jobs.json`（数据最全）或 `report.md`（最易读）
2. 把文件上传给 ChatGPT（或其他支持文件上传的 AI）
3. 输入你的要求，例如：

> 这是某公司的全部校招岗位。
> 请结合我的专业、实习经历和求职方向，
> 帮我筛选最匹配的岗位，并说明推荐原因。

**更多 Prompt 示例：**

岗位排序：

> 根据我的背景（计算机专业，有一段后端实习经历），
> 给这些岗位按匹配度从高到低排序，列出前 5 个并说明理由。

岗位对比：

> 对比这几个岗位的职责、要求和地点差异，
> 从发展前景角度给出你的建议。

筛选：

> 这些岗位里哪些不要求特定专业背景？
> 哪些工作地点在杭州？

## 11. 如果自动抓取成功

什么都不用做——直接到 `output/<公司名>/<时间戳>/` 目录里拿文件即可。

> **注意**：显示「完成 ✓」不代表一定拿到了全部完整 JD。对详情页有强访问限制的站点，运行可能以 `COMPLETE` + `LIST_ONLY` 结束：岗位列表完整、完整 JD 未批量获取——这不是采集失败。岗位名称、地点、类别、发布时间等信息仍可用于初筛或交给 ChatGPT/AI 分析；对感兴趣的岗位，再到招聘官网查看完整 JD（详见第 12 节末尾说明）。

## 12. 如果自动抓取失败：三层兜底机制

常见失败原因：

- 网站结构特殊，程序无法自动找到岗位数据来源
- 页面需要浏览器执行才能显示岗位
- 接口有签名 / 加密参数，无法自动发现
- 网站自身的访问限制

无论哪种原因，Job Extractor 都按三层顺序尝试获取数据：

```text
自动模式
   ↓  自动抓取失败时
Manual cURL fallback（手动 cURL 兜底）
   ↓  cURL 能读到岗位、但翻页请求无法安全复用时
Browser-assisted collection（浏览器辅助采集）
```

**第一层：自动模式**
正常情况下直接输入招聘页面 URL，由系统自动处理——识别网站、找到岗位数据来源、翻页抓取、抓取完整 JD（即第 7 节的 5 个阶段）。

**第二层：Manual cURL fallback**
自动抓取失败时，可以把浏览器开发者工具里的岗位列表请求复制为 cURL 交给程序继续采集：

- **List cURL** = 岗位列表请求（职位名称、岗位 ID、分页信息等）
- **Detail cURL** = 单个岗位的详情请求（完整 JD / 岗位描述）

如果 cURL 本身无效，程序会显示友好错误并允许你重新粘贴，不会继续升级。

程序失败时会**在终端显示具体的失败原因**，而不是静默退出，可据此判断走到了哪一层。

完整操作步骤统一见：[docs/MANUAL_CURL.md](MANUAL_CURL.md)

**第三层：Browser-assisted collection（浏览器辅助采集）**
有些网站的翻页请求带有动态验证信息：cURL 能正常读取当前页，但用同一请求继续翻页会被网站拒绝。此时程序会明确提示，可切换到浏览器辅助采集。你只需要：

1. 像平常一样输入 URL；程序判断需要浏览器辅助时，CLI 会显示浏览器辅助选项（输入 URL → 选择「开始浏览器辅助」；如已有 HAR，也可直接选「我已经有 HAR」）
2. 选择后，CLI 会**自动把「自动翻页助手」复制到剪贴板**，并在你的正常浏览器中打开职位列表页
3. 在**正常浏览器**里打开开发者工具的 Network 录制，在网站自己的控件里把每页数量调到最大（例如 50 条/页），再把剪贴板里的助手代码粘贴到 Console 运行——它只点击网站自己的翻页按钮，直到最后一页；如果网站弹出验证，会**自动暂停等你处理**
4. 网站出现验证时，由**你本人正常完成验证**。程序不会、也不会尝试替你绕过任何验证
5. 验证完成后助手自动继续翻页，翻完会停在最后一页
6. 在开发者工具里把 Network 记录**导出为 HAR 文件**
7. 回到 CLI 选择该 HAR 文件——导入是**纯离线解析**，不会重新发起任何网络请求

同一页如果先失败、后成功（例如验证后重试成功），导入时会**优先选用成功的那次响应**；跨页重复岗位会按稳定 ID 去重并在 `collection.json` 里记录审计。

**List-only 结果说明**：部分受限站点的详情页无法安全批量获取。此时导入 HAR 后的运行可能以 `COMPLETE` + `LIST_ONLY` 结束——岗位列表完整（名称、地点、类别、发布时间等），完整 JD 未批量获取。这不是采集失败：可用导出的 List 做初筛或交给 ChatGPT/AI 分析，对感兴趣的岗位再到招聘官网查看完整 JD。Job Extractor 不会为获取 JD 绕过任何验证或访问限制。

> 从浏览器复制的 cURL 和导出的 HAR 都可能包含 Cookie、Token、`Authorization`、Session 等敏感信息，不要直接上传到 GitHub、Issue、日志或公开聊天；分享前先检查并删除这些内容（详见第 19 节安全提醒）。Job Extractor 的输出文件不会包含这些值。

## 13. `_unknown` 是什么

系统会尽量从岗位数据、招聘站点信息或招聘域名中识别可用的公司/品牌名，多数情况下运行目录会直接以公司或品牌命名（如 `output/深圳市睿联技术有限公司/<时间戳>/`）。

`output/_unknown/<时间戳>/` 是**最后兜底**：本次运行确实没有可靠的公司识别依据，无法命名目录（采集本身是成功的）。打开里面的 `jobs.json` / `report.md` 检查内容即可；`_unknown` 不应是常态。

## 14. `_failed` 是什么

`output/_failed/<公司名或 _unknown>/<时间戳>/` 表示：**采集过程未能正常完成**。目录里只有一个 `error_report.json`，记录了失败发生在哪个环节以及具体原因。

可以把它作为排查线索；如果原因不明确，参考[常见问题](TROUBLESHOOTING.md)（待完成）或尝试第 12 节的手动 cURL 兜底。

## 15. 第二次怎么运行

每次打开新终端都需要先重新激活虚拟环境：

**Mac / Linux：**

```bash
cd job_extractor
source .venv/bin/activate
```

**Windows（PowerShell）：**

```powershell
cd job_extractor
.venv\Scripts\Activate.ps1
```

然后运行新 URL 即可：

```bash
python main.py "另一个招聘网站URL"
```

每次运行都会在 `output/` 下生成一个新的时间戳目录，互不影响。

## 16. 如何停止运行

在终端按 **Ctrl + C** 即可中断当前运行。程序会显示中断信息并正常退出；已经抓到的数据是否落盘取决于中断发生的阶段——中断后的目录可能不存在或不完整。

## 17. 如何更新项目

```bash
git pull
pip install -r requirements.txt
```

## 18. 常见问题

安装或运行遇到问题时，参见：[docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md)（待完成）

采集失败时，终端会显示简短的原因分类（不是内部报错堆栈）。常见分类与含义：

| 提示 | 含义 | 你可以做什么 |
| --- | --- | --- |
| 自动采集未完成 | 程序无法自动获取该网站的岗位数据 | 按提示切换 cURL 兜底 |
| 无法从这条 cURL 识别有效岗位数据 | 粘贴的 cURL 本身无效（过期、复制错误或返回的不是岗位列表） | 重新在浏览器里复制一次岗位列表请求的 cURL |
| 当前 cURL 可以读取岗位，但分页请求无法安全复用 | 网站翻页请求带动态验证信息 | 按提示切换浏览器辅助采集（见第 12 节第三层） |
| 浏览器采集结果无法读取 | 导入的 HAR 文件损坏或格式不对 | 重新从浏览器导出一次 HAR |
| 浏览器采集结果中没有找到岗位数据 | HAR 里没有可识别的岗位列表响应 | 确认导出前已打开职位列表页并完成翻页 |

如果原因不明确，参考[常见问题](TROUBLESHOOTING.md)（待完成）或尝试第 12 节的兜底方式。

## 19. 安全提醒

**绝对不要公开分享以下内容**（包括发到公开 GitHub Issue、聊天群或截图里）：

- Cookie
- Token
- `Authorization` 请求头
- 登录账号 / 密码
- **包含认证信息的 cURL 命令**（从浏览器复制的 cURL 里常带 Cookie，分享前务必删除 `-H 'Cookie: ...'` 等请求头）

相关说明见 [SECURITY.md](../SECURITY.md)（待添加）。
