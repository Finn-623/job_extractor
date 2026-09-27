# Usage Guide

This guide covers normal use of Job Extractor after installation. It works with
public recruitment pages only; do not use it to bypass login, CAPTCHA, or other
access controls.

## Quick start

From the repository root, activate the virtual environment and run a public
recruitment URL:

```bash
source .venv/bin/activate
python main.py "https://example.com/jobs"
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python main.py "https://example.com/jobs"
```

The command prints five collection stages and writes results below `output/`.

## Platform URLs

Pass the public recruitment URL directly. The adapter is selected
automatically when its platform is recognised.

```bash
# Moka, including campus_apply routes
python main.py "https://app.mokahr.com/campus-recruitment/acme/123"
python main.py "https://app.mokahr.com/campus_apply/acme/123"

# Feishu / Lark, Zhiye, Beisen, or an unknown public career site
python main.py "https://careers.example.com/jobs"
python main.py "https://example.zhiye.com/campus/jobs"
```

The final two examples are URL shapes, not promises that every host using that
shape is supported. Unknown sites use the experimental Generic engine.

## Generic multi-scope sites

When a site has separate campus, social, or specialist recruitment areas, the
Generic engine can discover and collect each explicit scope before merging
duplicate stable job IDs. Select a scope only when you want a narrower run:

```bash
python main.py "https://example.com/jobs" --scope campus
python main.py "https://example.com/jobs" --scope social
python main.py "https://example.com/jobs" --scope all
```

`collection.json` records raw rows, unique jobs, scope progress, and
cross-scope merges. On a group portal, the output directory uses the portal's
site company when evidence supports it; individual jobs retain their own
employer where the source provides one.

## Manual cURL fallback

If automatic extraction cannot safely obtain the list and detail data, copy a
sanitized public list request and detail request from browser developer tools:

```bash
python main.py --list-curl "<sanitized-list-curl>" --detail-curl "<sanitized-detail-curl>"
```

The cURL text is parsed, never executed by a shell. Remove `Cookie`,
`Authorization`, tokens, signatures, and private parameters before sharing or
saving it. See [MANUAL_CURL.md](MANUAL_CURL.md) for the step-by-step flow.

## Output files

Each successful run creates `output/<company>/<timestamp>/` with:

- `jobs.json` — full structured records, best for automation or AI workflows.
- `jobs.csv` — one flat row per job for spreadsheets.
- `jobs.xlsx` — workbook for review.
- `report.md` — human-readable collection and data-quality summary.
- `collection.json` — timing, pagination, and deduplication audit metadata.

Failed runs write an `error_report.json` under `output/_failed/`. When a site
company cannot be identified, a completed run may be placed under
`output/_unknown/`.

## Debug trace

Normal CLI runs do not print internal traces. For one diagnostic run:

```bash
JOB_EXTRACTOR_TRACE=1 python main.py "https://example.com/jobs"
```

Trace output is intended for troubleshooting and may contain request structure
details. Review and redact it before sharing. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common errors and platform limits.
