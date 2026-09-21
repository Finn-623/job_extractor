# Contributing

感谢关注本项目！以下是贡献前需要了解的内容。

## Before you start

无论提交 Issue、Pull Request 还是测试样本，都**不要**包含：

- 真实 Cookie / Token / API Key
- 真实私人 output 数据
- 公司内部数据
- 带认证上下文的 cURL 命令

详细清单见 [SECURITY.md](SECURITY.md)。

## Development setup

安装步骤与 [README](README.md) / [用户指南](docs/USER_GUIDE.md) 相同：

```bash
git clone https://github.com/Finn-623/job_extractor.git
cd job_extractor
python3 -m venv .venv        # Windows: python -m venv .venv
source .venv/bin/activate    # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Adding a new adapter

想为新的招聘平台增加支持时，按以下高层步骤：

1. 在 `job_extractor/adapters/` 下新增 adapter，参考现有平台实现
2. 把它注册到 `job_extractor/adapters/__init__.py` 的默认 registry
3. 为该 adapter 增加测试（使用虚构 / 脱敏的响应样本，不要录制真实凭证）
4. 验证岗位列表提取（list extraction）
5. 验证详情 / JD 提取（detail extraction）
6. 验证分页行为
7. 验证最终 output 文件（jobs.json / jobs.csv / jobs.xlsx / report.md / collection.json）

每个环节都可以先用 `python main.py "URL" --discover` 只观测不抓取。

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/
```

提交 Pull Request 前**所有测试必须通过**。当前基线约 870 条测试；具体数字会随开发变化，以你本地运行结果为准——全绿即可。

## Pull Request

PR 请满足：

- 改动范围清晰，一个 PR 聚焦一件事
- 新功能 / 修复附带对应测试
- **不包含任何私人数据或真实凭证**
- 不做与目标无关的大规模重构
