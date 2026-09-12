# Job Extractor

## Platforms

- Zhiye — READY
- Moka — READY
- Feishu — READY (Playwright browser runtime)
- Generic — detection only

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
