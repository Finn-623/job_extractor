# Job Extractor

**English** | [中文文档](README.zh-CN.md)

Extract job listings and full job descriptions from recruitment websites into structured files for AI-assisted job recommendation, filtering and analysis.

输入招聘网站 URL，自动提取岗位列表和完整 JD，并生成可直接交给 GPT 等 AI 使用的结构化文件，用于岗位推荐、筛选和分析。

## Why Job Extractor?

Recruitment websites differ wildly from company to company. When a company posts dozens or hundreds of openings, reading job descriptions one by one is slow and painful.

Job Extractor turns a recruitment page into clean, structured data so that you — or an AI assistant — can process all jobs at once:

```
Recruitment Website
      ↓
 Job Extractor
      ↓
Structured Job Data   (jobs.json / jobs.csv / jobs.xlsx / report.md)
      ↓
    GPT / AI
      ↓
Job Recommendation / Filtering / Analysis
```

You no longer read jobs one by one. Extract once, then let AI do the reading.

## Features

- **Automatic job list discovery** — analyzes the site, finds the underlying job list API, and collects all postings with pagination
- **Full JD extraction** — fetches complete job descriptions (responsibilities / requirements), not just titles
- **Multi-platform support** — dedicated adapters for several recruitment platforms, plus a generic engine for unknown sites
- **Deduplication** — repeated postings across pages are merged and audited
- **Browser fallback** — JavaScript-rendered sites fall back to a headless browser automatically
- **Manual cURL fallback** — protected sites accept a cURL request copied from your browser
- **Multiple export formats** — JSON, CSV, XLSX and a Markdown report per run

## AI Workflow

Job Extractor handles extraction and structuring. The AI handles recommendation and analysis:

1. Run Job Extractor on a recruitment URL
2. Open the run directory and take `jobs.json` (or `jobs.csv` / `report.md`)
3. Upload the file to ChatGPT or another AI assistant
4. Ask, for example:

   > "Based on my background and career goals, rank these jobs and recommend the best matches."

## Supported Platforms

Verified recruitment platforms:

- **Moka** — Moka-powered career sites
- **Feishu / Lark** recruitment sites
- **Zhiye** — `*.zhiye.com` career sites
- **Beisen CMS** — Beisen-powered career sites

Other websites go through the **generic engine**, which auto-discovers the job list API and falls back to a headless browser when needed. Generic success depends on the site — treat it as experimental rather than guaranteed. Job Extractor does not promise support for every recruitment website.

## Installation

> **Python 3.10 or newer is required.** Check with `python3 --version`.
> If your system `python3` is older than 3.10, install Python 3.10+ first.

macOS / Linux:

```bash
git clone https://github.com/Finn-623/job_extractor.git
cd job_extractor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Windows (PowerShell):

```powershell
git clone https://github.com/Finn-623/job_extractor.git
cd job_extractor
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Quick Start

```bash
python main.py "https://example.com/jobs"
```

> `example.com` is a format illustration only, not a verified site. Use a real recruitment page URL.

Useful options:

```bash
python main.py "URL" --scope campus     # resolve an ambiguous scope: campus, social, intern, all
python main.py "URL" --discover         # observe and report probable job APIs only
python main.py --list-curl "<curl>" --detail-curl "<curl>"   # manual cURL fallback
```

No `PYTHONPATH` or other environment setup is required — run it from the repository root.

> If a site fails to extract automatically, Job Extractor retries it with a headless browser — and, as a last resort, accepts a cURL request copied from your browser. See [Manual cURL Fallback](#manual-curl-fallback).

## Output

Each successful run creates a timestamped directory:

```
output/
└── Company Name/
    └── 2026-01-01_120000/
        ├── jobs.json         # machine-readable, best for AI / automation
        ├── jobs.csv          # flat table for spreadsheets and quick analysis
        ├── jobs.xlsx         # Excel workbook for human review
        ├── report.md         # readable run summary, fine to upload to an AI
        └── collection.json   # collection process metadata (timing, metrics, audit)
```

- **jobs.json** — structured job records including full JD text; the primary file for AI workflows
- **jobs.csv** — one row per job for filtering and pivoting
- **jobs.xlsx** — the same data formatted for Excel users
- **report.md** — run overview: totals, completeness flags, per-job summary
- **collection.json** — how the data was collected (mode, elapsed time, dedup audit)

## Failed / Unknown Output

- `output/_unknown/<timestamp>/` — the run could not determine a company name to name the directory
- `output/_failed/<company-or-_unknown>/<timestamp>/` — the run failed; it contains only `error_report.json` describing what went wrong

Successful runs never write into these directories.

## Automatic Extraction

Given a recruitment URL, Job Extractor observes how the page loads its job list, identifies the job list API behind it, builds a collection plan, and pages through the full list. Job details are then fetched and cleaned into structured records with completeness flags, so you can see exactly how much of each JD was captured. Job descriptions are never fabricated — only what the source site provides is recorded.

## Browser Fallback

Some sites render their job list only in the browser. When plain HTTP collection is not enough, Job Extractor retries the page with a headless Chromium and reads the rendered result.

## Manual cURL Fallback

Job Extractor works in three layers, tried in order:

```
Automatic extraction
      ↓  if the site cannot be auto-discovered
Browser fallback
      ↓  if rendered pages still yield no data
Manual cURL fallback
```

Manual cURL is the last resort. Use it when:

- automatic extraction fails on the website
- the browser fallback still cannot obtain data
- the site loads its job list through front-end API / XHR requests

Two kinds of cURL are involved:

- **List cURL** — the request that returns the job list (job titles, IDs, pagination)
- **Detail cURL** — the request that returns one job's full details (the complete JD / job description)

Copy the requests from your browser's developer tools as cURL and pass them to the program with `--list-curl` / `--detail-curl` (or the `--list-curl-file` / `--detail-curl-file` variants). The cURL is parsed but never shell-executed.

Detailed guide: [docs/MANUAL_CURL.md](docs/MANUAL_CURL.md)

> **Security warning:** copied cURL commands may contain `Cookie` headers, `Authorization` headers, tokens and session information. Never publish or share raw cURL commands without reviewing and removing sensitive information first.

## Example Output

You can inspect the [example output](examples/example_output/) before running the extractor — fully fictional data (Example Robotics Ltd.) generated by the same export pipeline: `jobs.json`, `jobs.csv`, `jobs.xlsx`, `report.md`, `collection.json`.

## Documentation

- [User Guide](docs/USER_GUIDE.md)
- [Manual cURL Guide](docs/MANUAL_CURL.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## Project Structure

```
job_extractor/     # core package
tests/             # test suite
scripts/           # benchmark utilities
docs/              # documentation
examples/          # usage examples
output/            # run results (generated at runtime)
main.py            # CLI entry point
```

## Limitations

- No guarantee for every recruitment website — site structures vary and change
- A redesigned page can break extraction until the tool is updated
- Some sites require the browser fallback or manual cURL fallback
- The project does **not** bypass login, CAPTCHAs, or other access controls
- Rate limits and the terms of the target website still apply

## Security

Never share or commit cookies, tokens, `Authorization` headers, or cURL commands that contain authentication information. See [SECURITY.md](SECURITY.md).

## Disclaimer

This project is intended for personal study, research, and data organization of publicly available recruitment information. You are responsible for complying with the target website's terms of service, robots policy, access frequency limits, and applicable laws. The project is not designed to bypass login, CAPTCHAs, or other access controls.

## License

Released under the [MIT License](LICENSE).
