# Job Extractor (V1)

对官方招聘页面 URL 自动发现数据源、采集全量职位列表、做有证据支撑的详情补全，并输出诚实标注完整性的报告（JSON / Excel / Markdown）。

## Platforms

- Zhiye — READY
- Moka — READY
- Feishu — READY (Playwright browser runtime)
- Generic — READY (V1：source discovery → generic plan → collection → detail enrichment → reporting)

## V1 使用

```bash
python main.py "<招聘页面URL>"
```

示例：

```bash
python main.py "https://careers.geelytech.com/campus"
```

默认运行全流程：source discovery → 采集计划 → 全量列表采集 →（有证据时）详情补全 → 报告输出。也可分步：`--discover`（只观测数据源）、`--plan`（生成采集计划）、`--collect-generic`（发现并执行采集）。

### 输出位置与格式

每次采集在 `~/.job_extractor/output/` 生成一个运行目录：

```text
jobs.json     机器可读，source of truth
jobs.xlsx     Summary / Jobs / Data Quality 三个 sheet
report.md     Markdown 报告（>50 jobs 自动切换紧凑摘要）
```

### Completeness / fail-closed 语义

- 每个 job 的 JD 完整性逐条诚实计数：`FULL_TEXT`（全文）、`SUMMARY`（源站只给短摘要）、`ABSENT`（无 JD 字段）。
- 源站只提供短摘要时，系统**不会自行补全 JD**，只如实标记 incomplete；缺证据时 detail enrichment 不猜测。
- 无伪造 JD：所有 JD 内容均来自源站字段。

### 已知边界（Post-V1）

- 源站仅提供 teaser/短摘要（如 AECC）→ 该站 JD 完整率受限，fail-closed。
- 源站无可观察的 identity-bound detail source（如 Sinomach）→ JD 仅限列表级。
- 上游偶发重复投递（如 Hisense）→ 稳定 ID 去重正确合并，unique 数可能低于 reported_total。
- 少量 DETAIL_EMPTY → fail-closed 标记 incomplete。

## Unified JSON output

Every completed run exports the same top-level fields: `source_url`, `platform`,
`company`, `scope`, `metrics`, `total_expected`, `total_fetched`, `total_unique`,
`status`, `errors`, and `jobs`. Platform response data remains available under
each job's `raw_data`; response encryption keys, cookies, headers, and tokens are
never included.

## Collection reports

Every completed collection creates one cross-platform-safe run directory under
`~/.job_extractor/output/` containing:

```text
jobs.json
jobs.xlsx
report.md
```

JSON remains the machine-readable source of truth. The Excel workbook contains
`Summary`, `Jobs`, and `Data Quality` sheets; Markdown automatically switches to
compact job summaries when a run contains more than 50 jobs.

跨平台招聘岗位提取工具。当前已支持 Zhiye、Moka 和 Feishu 平台的岗位采集；Generic 暂时只做平台识别。

## 使用

```bash
python main.py <URL>
```

处理流程：

```text
URL Validation
→ Adapter Registry
→ Platform Detection
→ Tenant Configuration
→ Recruitment Scope
→ API Collection
→ Normalization
→ JSON Export
```

Zhiye 会根据 URL 中的 `/campus/`、`/social/`、`/intern/` 路由限定招聘范围。列表已包含完整职责和要求时直接使用列表数据；缺少 JD 字段时才调用详情 API。

## macOS 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py https://example.com/jobs
```

## Windows 安装

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python main.py https://example.com/jobs
```

## 测试

```bash
pytest -q
```
