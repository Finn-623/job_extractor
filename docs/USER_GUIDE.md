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

## 8. 去哪里看结果

所有结果保存在项目目录下的 `output/` 中。正常完成的运行类似：

```text
output/
└── Company Name/            ← 公司名目录
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

## 12. 如果自动抓取失败

常见原因：

- 网站结构特殊，程序无法自动找到岗位数据来源
- 页面需要浏览器执行才能显示岗位（程序会自动使用无头浏览器重试）
- 接口有签名 / 加密参数，无法自动发现
- 网站自身的访问限制

程序失败时会**在终端显示具体的失败原因**，而不是静默退出。如果自动模式最终失败，可以尝试**手动 cURL 兜底**：从浏览器开发者工具复制岗位列表请求为 cURL，交给程序继续采集。

完整操作教程见：[docs/MANUAL_CURL.md](MANUAL_CURL.md)（待完成）

## 13. `_unknown` 是什么

`output/_unknown/<时间戳>/` 表示：**采集本身成功产出了结果，但程序无法可靠识别出这些岗位属于哪家公司**（例如页面上没有明确的公司名），因此无法用公司名命名目录，结果被放到了 `_unknown` 里。

你可以打开里面的 `jobs.json` / `report.md` 检查内容是否符合预期。

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

## 19. 安全提醒

**绝对不要公开分享以下内容**（包括发到公开 GitHub Issue、聊天群或截图里）：

- Cookie
- Token
- `Authorization` 请求头
- 登录账号 / 密码
- **包含认证信息的 cURL 命令**（从浏览器复制的 cURL 里常带 Cookie，分享前务必删除 `-H 'Cookie: ...'` 等请求头）

相关说明见 [SECURITY.md](../SECURITY.md)（待添加）。
