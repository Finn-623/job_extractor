# Security Policy

## 支持的版本

本项目目前处于早期发布阶段，安全修复会直接应用到 `main` 分支。请始终保持本地代码为最新版本（`git pull`）。

## 不要公开提交的敏感内容

从浏览器复制的招聘网站请求中常带有你的**登录或会话凭证**。以下内容**绝对不要**提交到本仓库、公开 Issue、Pull Request、截图或聊天群：

- `Cookie`
- `Authorization`
- `Bearer Token`
- API Key
- `Session`
- `Signature`
- 登录凭证（账号 / 密码 / 短信验证码）
- 带认证上下文的完整 cURL 命令
- 企业内部数据
- 非公开招聘数据

## 提 Issue 前先脱敏

需要提供 cURL 或日志时，先删除或替换所有敏感请求头与参数，例如：

```text
Cookie: [REDACTED]
Authorization: Bearer [REDACTED]
token=[REDACTED]
```

快速自查：cURL 或日志中出现 `Cookie:`、`Authorization:`、`Bearer`、`token`、`session`、`signature`、`x-auth`、`csrf` 时，默认按敏感信息处理。详见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) 的检查清单。

## 允许的使用范围

本项目用于对**公开发布**的招聘信息进行个人学习、研究和数据整理。

本项目**不用于、也不得用于**绕过：

- 登录
- CAPTCHA（验证码）
- 访问控制
- 网站安全机制

目标网站的服务条款、robots 政策与访问频率限制仍然适用，由使用者自行遵守。

## 报告安全问题

请不要通过公开 Issue 报告安全漏洞。如果本仓库已启用私有安全通告（GitHub private security advisory / "Report a vulnerability"），请优先通过该渠道报告；如尚未启用，可在 Issue 中说明你希望私下沟通，由维护者安排后续渠道。
