# Changelog

本项目所有重要变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [2.1.0] - 2026-09-29

### Added

- 渐进式兜底采集：自动采集 → 手动 cURL → 浏览器辅助采集（HAR 导入）。
- 通用 HAR 导入器：离线解析浏览器导出的网络记录，自动识别可信岗位列表
  响应（支持 `list` / `result.list` 等结构），同一页先失败后成功时优先
  选用成功响应，跨页重复岗位按稳定 ID 去重。
- 分页重放被拒的错误分类（`CURL_PAGINATION_NOT_REPLAYABLE`）：cURL 当前页
  有效但动态分页无法安全复用时的独立状态，与 cURL 本身无效明确区分。
- 浏览器自动翻页助手：只操作网站自身分页控件；遇到网站验证自动暂停，
  由用户手动完成后自动继续；最多 100 页保护。
- 采集 API 桥接（自动 / cURL / HAR 三个端点 + 分页助手代码下发），
  复用正式采集、导入与导出管线。
- 前端三级渐进兜底采集流程（自动 → cURL → 浏览器辅助）。

### Improved

- 三种来源（自动 / cURL / HAR）统一进入同一 Job 归一化、去重与导出管线，
  输出结构完全一致。
- `workLocation` 字段归一化（通用名，三种来源同样受益）。
- HAR / cURL 统一使用正式 ReportManager 导出管线。
- 兜底过程的指标与审计信息（fallback metadata）。

### Security / Safety

- HAR / cURL 中的 Cookie、`Authorization`、anti-bot 参数等敏感请求内容
  不会写入输出文件或日志。
- 浏览器辅助模式不生成 anti-bot token，不自动绕过验证码；网站验证始终
  由用户本人在正常浏览器中完成。

### Fixed

- `code` 作为稳定岗位 ID 的兼容识别。
- HAR 响应 `result.list` 等嵌套结构的解析。
- 同一页先失败后成功时成功响应的选择。
- HAR 中 `total` 为字符串形式时的处理。
- cURL 分页重放被拒与 cURL 无效的错误分类。


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
