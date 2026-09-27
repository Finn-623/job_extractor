# Changelog

本项目所有重要变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [2.0.0] - 2026-09-27

### Added

- Dedicated adapters for Moka (including `campus_apply`), Feishu / Lark,
  legacy Zhiye, and Beisen CmsPortal sites.
- Generic API discovery with best-effort browser and manual cURL fallbacks.
- Multi-scope generic collection, stable-ID deduplication, and cross-scope
  merge metrics.
- Site-level company identity for hosted group recruitment portals while
  preserving each posting's legal-entity employer.
- JSON, CSV, XLSX, Markdown report, and collection-audit outputs.
- Five-stage CLI progress display and opt-in diagnostic traces via
  `JOB_EXTRACTOR_TRACE=1`.

### Improved

- Generic multi-scope pagination requests use a safe larger page size where
  supported, while retaining authoritative-total completeness checks.
- Company resolution, JD completeness reporting, and output audit data for
  group recruitment sites.

### Fixed

- Internal scope and timing traces are silent during normal CLI runs.
- Hosted group recruitment sites no longer unnecessarily fall back to
  `_unknown` when evidence supports a site company.
