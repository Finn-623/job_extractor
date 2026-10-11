# Changelog

本项目所有重要变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [2.2.0-rc.1] - 2026-10-11

首个 GitHub Pre-release（Release Candidate）。内部版本显示 `2.2.0rc1`（PEP 440
写法），与本节标题对应的 Git Tag 写法 `v2.2.0-rc.1` 为同一版本号的两种等价拼写。

### Added

- JD 阶段软时间预算 `--jd-time-budget`（默认 300 秒）与预算到期策略
  `--jd-on-timeout ask|skip`（非 TTY 默认 skip，无人值守运行会自动跳过并保存）。
- Manual cURL Checkpoint（`output/_checkpoints/<run-id>/jd_checkpoint.json`）：
  JD 阶段部分完成或中断时自动保存岗位列表与逐任务状态（SUCCESS / FAILED / SKIPPED）。
- `--resume-checkpoint` 定向续抓：配合新的有效 `--detail-curl` / `--detail-curl-file`
  仅重试 checkpoint 中缺失、失败或被跳过的 JD；已成功任务保留，恢复运行产生
  独立的新 Checkpoint（记录 `resumed_from`）。
- JD 部分失败后的正式导出：结果状态为 `INCOMPLETE` 时仍输出全部五个正式文件，
  并与 Checkpoint 阶段标记 `JD_PARTIAL` / `JD_ABORTED` 明确区分。
- 五阶段 CLI 动态进度（ProgressReporter）：首次 Detail API 探测状态、
  基于近期吞吐率的 ETA、连续 15 秒无完成事件提示、预算到期后已启动任务
  安全收束的状态显示。
- 默认离线测试套件；真实站点测试改为 `pytest -m live` 显式 opt-in。

### Changed

- CLI 进度渲染统一为 ProgressReporter，TTY 输出自适应。
- Detail API 生命周期处理：探测状态显式化，失败分类与收束行为更准确。
- 完整回归默认不发起外网请求（离线 stub），live 测试独立运行。

### Fixed

- JD 补全失败导致无法正式导出的问题（部分失败仍正式导出）。
- 中断（含 KeyboardInterrupt）时 task_states 丢失的问题。
- UI 停留在过期进度的问题。
- 初始 ETA 严重失真的问题。
- 部分结果场景下并发指标统计不准确的问题。

### Known Limitations

- `--jd-time-budget` 是**软预算**：只约束 JD 补全阶段，已启动的请求会安全收束，
  不保证整个进程严格在预算时长内退出。
- Checkpoint 与 `--resume-checkpoint` 定向续抓目前仅在 Manual cURL 路径实现；
  自动模式、Generic / Provider 采集器不生成 checkpoint，也不全面支持上述能力。
- 真实外部招聘网站的完整回归（live 测试）本版本尚未重新执行通过。
- Hotjob 自动发现仍可能失败，需要 Manual cURL 兜底。

## [2.1.1] - 2026-10-02

### Changed

- 文档收口（docs-only patch，无业务代码变更）：
  - 说明 List-only 行为：受限站点可能以 `COMPLETE` + `LIST_ONLY` 结束——
    岗位列表完整采集、完整 JD 未批量获取，这不是采集失败；导出 List 仍可
    用于初筛或交给 AI 分析。
  - Browser Assist 说明更新为当前真实 CLI 流程（URL → CLI 显示浏览器辅助
    选项 → 助手复制到剪贴板 → 用户在正常浏览器完成人工步骤 → 导出 HAR →
    回到 CLI 导入），移除过时的 dashboard bridge 操作路径。
  - 新增公司/品牌输出目录命名说明（`output/<公司或品牌>/<时间戳>/`，
    `_unknown/` 仅为最后兜底）。
  - 故障排查更新：LIST_ONLY 结果状态说明与 `_unknown` 语义同步。

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
