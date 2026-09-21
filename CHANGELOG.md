# Changelog

本项目所有重要变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.0.0] - TBD

### Added

- Recruitment URL extraction（招聘 URL 识别与提取）
- Job list discovery（岗位列表自动发现）
- Pagination（自动分页抓取）
- Full JD / detail extraction（完整 JD / 岗位详情提取）
- Multiple recruitment platform adapters（多招聘平台适配器）
- Generic extraction engine（通用提取引擎，适用于未专门适配的网站）
- Browser fallback（无头浏览器兜底）
- Manual cURL fallback（手动 cURL 兜底）
- Deduplication / normalization（去重与数据规范化）
- JSON / CSV / XLSX / Markdown export（多格式导出）
- Structured output for AI workflows（面向 AI 工作流的结构化输出）
- Mac / Windows usage documentation（Mac / Windows 使用文档）

### Verified platforms

以下平台经过验证支持（不承诺支持所有网站）：

- Moka
- Feishu / Lark（飞书招聘）
- Zhiye（北森智业）
- Beisen CMS（北森云招聘）
