# Job Extractor

**English** | [中文文档](README.zh-CN.md)

Give Job Extractor a public recruitment URL. It identifies the platform or data
source, extracts job listings and complete job descriptions, and exports
structured results for review, filtering, and analysis.

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
- **Multi-scope collection** — explicit campus, social, and specialist scopes are collected before a site-level merge
- **Company-aware output** — hosted group sites retain the portal owner and each posting's own legal-entity employer
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

| Platform | Status | Collection mode | Notes |
| --- | --- | --- | --- |
| Moka | Supported | Dedicated adapter | Includes `campus_apply` routes. |
| Feishu / Lark | Supported | Dedicated adapter | Uses the public recruitment flow. |
| Zhiye | Supported | Dedicated adapter | Supports legacy `*.zhiye.com` career sites. |
| Beisen CmsPortal | Supported | Fingerprinted adapter | Routed only after a CmsPortal fingerprint check. |
| Other public career sites | Experimental | Generic discovery | Best-effort API/browser discovery; not guaranteed for every site. |

The generic engine can discover explicit recruitment scopes (for example,
campus, social, or specialist hiring), collect each scope, and merge duplicate
stable IDs across scopes. It preserves scope provenance and reports merge
metrics in `collection.json`.

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

Platform URL examples (replace the sample tenant values with a public career
page you are entitled to access):

```bash
python main.py "https://app.mokahr.com/campus-recruitment/acme/123"
python main.py "https://app.mokahr.com/campus_apply/acme/123"
python main.py "https://example.zhiye.com/campus/jobs"
python main.py "https://careers.example.com/jobs"
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

- `output/_unknown/<timestamp>/` — the final fallback when no reliable company or brand identity can be determined for the directory name
- `output/_failed/<company-or-_unknown>/<timestamp>/` — the run failed; it contains only `error_report.json` describing what went wrong

Successful runs never write into these directories.

## Company / Brand Output Naming

Each run directory is normally named after the company or brand behind the
recruitment site, for example:

```
output/深圳市睿联技术有限公司/2026-01-01_120000/
output/Usmile/2026-01-01_120000/
```

Job Extractor tries to resolve a useful company or brand name from explicit
recruitment data, the recruitment site's own identity, or the recruitment
domain. In most cases this produces a usable name; `_unknown/` is only the
last-resort fallback when nothing reliable can be determined — it should be
the exception, not the norm.

## List-only Sites

Some recruitment sites allow Job Extractor to collect the complete job list,
while their detail pages have stronger browser verification or access
restrictions. In this case Job Extractor may finish with:

- collection status: `COMPLETE`
- JD strategy: `LIST_ONLY`

This means:

- the job list was collected successfully (titles, locations, categories,
  posting dates, etc.)
- full JD pages were not batch-collected
- **this is not a failed collection** — the list itself is the result
- the exported list can still be used for initial filtering or
  AI-assisted analysis (hand it to ChatGPT and let it rank and shortlist)
- open the official recruitment site for the full JD of the jobs you are
  interested in

Job Extractor never bypasses verification or access restrictions to obtain
these JDs; `LIST_ONLY` is the honest record of what could be collected.

## Automatic Extraction

Given a recruitment URL, Job Extractor observes how the page loads its job list, identifies the job list API behind it, builds a collection plan, and pages through the full list. Job details are then fetched and cleaned into structured records with completeness flags, so you can see exactly how much of each JD was captured. Job descriptions are never fabricated — only what the source site provides is recorded.

## Debugging

Normal runs keep internal discovery diagnostics silent. To diagnose a single
public run, enable the redacted trace gate:

```bash
JOB_EXTRACTOR_TRACE=1 python main.py "https://example.com/jobs"
```

Review trace output before sharing it. See [Troubleshooting](docs/TROUBLESHOOTING.md)
for installation, output, and extraction issues.

## Browser Fallback

Some sites render their job list only in the browser. When plain HTTP collection is not enough, Job Extractor retries the page with a headless Chromium and reads the rendered result.

## Manual cURL Fallback

Job Extractor works in three user-facing layers, tried in order:

```
Automatic extraction
      ↓  if the site cannot be auto-discovered
Manual cURL fallback
      ↓  if the cURL reads jobs but pagination cannot be safely replayed
Browser-assisted collection
```

### Automatic Collection

The default. Job Extractor identifies the platform or data source, pages through the job list, fetches job details and exports the results — no manual input needed.

### cURL Fallback

If automatic extraction fails, you can paste a cURL request copied from your browser's developer tools. Job Extractor parses it (never shell-executes it) and continues the collection. Use `--list-curl` / `--detail-curl` (or the `--list-curl-file` / `--detail-curl-file` variants), or paste it when the interactive fallback prompts you.

- **List cURL** — the request that returns the job list (job titles, IDs, pagination)
- **Detail cURL** — the request that returns one job's full details (the complete JD)

If the cURL itself is not valid, the run reports `MANUAL_CURL_INVALID`-style errors and you can re-paste.

### Browser-Assisted Collection

Some sites sign their pagination requests with dynamic verification values. In that case the list cURL can read the current page, but replaying the same request for the next page is rejected (`CURL_PAGINATION_NOT_REPLAYABLE`). The CLI then offers the browser-assisted layer:

1. Enter the URL as usual; when Job Extractor determines browser assistance is needed, the CLI shows the browser-assist option
2. Choose it and the CLI **copies the bundled pagination helper to your clipboard** and opens the job list page in your **normal browser**
3. In the browser: turn on Network recording, paste the helper into the Console and run it — it clicks the site's own next-page control until the last page, and pauses automatically if the site shows a verification challenge
4. **The site's verification is completed by you, manually, in your normal browser.** Job Extractor never generates anti-bot tokens, never bypasses captchas, and never touches your cookies
5. When pagination finishes, export the Network recording as a **HAR file**
6. Return to the CLI and select the HAR file (or start with "I already have a HAR" if you exported one earlier) — Job Extractor ingests it **fully offline** through the formal importer: pages where a request first failed and later succeeded use the successful response; duplicates across pages are deduplicated by stable ID

A restricted site whose detail pages cannot be safely batch-fetched may finish as a List-only result — see [List-only Sites](#list-only-sites).

The importer only accepts credible job-list responses; it never replays requests and never stores cookie/authorization/anti-bot values from the HAR.

> **Security warning:** copied cURL commands and exported HAR files may contain `Cookie` headers, `Authorization` headers, tokens and session information. Never publish or share them without reviewing and removing sensitive information first. Outputs produced by Job Extractor never contain these values.

Detailed guide: [docs/MANUAL_CURL.md](docs/MANUAL_CURL.md) · [User Guide](docs/USER_GUIDE.md)

## Example Output

You can inspect the [example output](examples/example_output/) before running the extractor — fully fictional data (Example Robotics Ltd.) generated by the same export pipeline: `jobs.json`, `jobs.csv`, `jobs.xlsx`, `report.md`, `collection.json`.

## Known Limitations

- **Dynamic verification sites**: some sites sign list/detail requests with dynamic anti-bot values. Automatic collection and cURL replay can be rate-limited or rejected; the browser-assisted layer exists exactly for these cases.
- **JD completeness may be partial**: when a site's detail API is protected, the full job description may be incomplete even though the job list itself is fully collected. Completeness flags in `collection.json` and `report.md` always say exactly what was captured.
- **Browser pagination helper**: the bundled helper is validated against specific pagination DOM types (for example rocket-* style pagination components). It is not a universal auto-pager, and it never handles captchas — the site's own verification is always completed by you.
- **Generic engine is best-effort**: unknown sites are handled by generic discovery and are not guaranteed to work for every site.
- **List-only completions**: on strongly restricted sites the result may be `COMPLETE` + `LIST_ONLY` — a fully collected list without batch-fetched JDs (see [List-only Sites](#list-only-sites)).

## Documentation

- [User Guide](docs/USER_GUIDE.md)
- [Usage Guide](docs/USAGE.md)
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

## Feedback

Report reproducible bugs or request support for a new public recruitment
platform through the repository's [issue templates](https://github.com/Finn-623/job_extractor/issues/new/choose). Never include cookies, tokens, account details, or unsanitized cURL commands.
