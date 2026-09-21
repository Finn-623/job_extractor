# Troubleshooting（常见问题速查）

遇到报错先在这里找。每条按 **现象 → 可能原因 → 解决** 给出最短处理办法。

- [用户指南](USER_GUIDE.md) | [Manual cURL 指南](MANUAL_CURL.md) | [中文 README](../README.zh-CN.md)

## 安装与启动

### 问题 1：`python: command not found`

**现象**：终端提示 `command not found: python`（或 `python 不是内部或外部命令`）。

**可能原因**
1. Mac 上系统自带的命令名是 `python3`
2. Windows 上 Python 未安装，或没加入 PATH

**解决**

```bash
python --version    # Windows 先试这个
python3 --version   # Mac 先试这个
```

- Mac：改用 `python3` 运行本文所有 `python` 命令
- Windows：两条都报错说明 Python 未装好，去 [python.org](https://www.python.org/downloads/) 安装，安装时**勾选 "Add Python to PATH"**，然后重开终端再试

### 问题 2：无法创建 / 激活虚拟环境

**现象**：`python -m venv` 报错，或激活后提示符前没有 `(.venv)`。

**可能原因**
1. 没在项目根目录（`job_extractor` 文件夹）里
2. PowerShell 禁止运行脚本

**解决**

```bash
# Mac / Linux
source .venv/bin/activate

# Windows（cmd）
.venv\Scripts\activate.bat

# Windows（PowerShell）
.venv\Scripts\Activate.ps1
```

- PowerShell 报"禁止运行脚本"时，执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

这只影响当前用户、允许本地脚本运行，是官方推荐的安全设置。然后重试激活。

### 问题 3：`ModuleNotFoundError`

**现象**：运行时报 `ModuleNotFoundError: No module named 'typer'`（或 `httpx`、`pydantic` 等）。

**可能原因**
1. 依赖没装全
2. 虚拟环境没激活，pip 装到了别处

**解决**

```bash
pip install -r requirements.txt
```

先确认提示符前有 `(.venv)`。如果还没有，回到问题 2 激活后再安装。

### 问题 4：Playwright / Chromium 未安装

**现象**：涉及浏览器兜底的功能报错，提示找不到浏览器或 `playwright` 模块。

**可能原因**
1. 只执行了 `pip install`，没下载浏览器内核
2. Chromium 下载中断

**解决**

```bash
playwright install chromium
```

如果提示 `playwright` 命令不存在，用等价的跨平台写法：

```bash
python -m playwright install chromium
```

### 问题 5：`main.py` 无法启动

**现象**：`python main.py ...` 直接报错，还没进入抓取流程。

**可能原因**
1. 不在项目根目录
2. Python 版本低于 3.10
3. 依赖未安装

**解决**

```bash
cd job_extractor          # 确认在项目根目录（能看到 main.py）
python --version          # 需要 3.10 或更高
pip install -r requirements.txt
```

### 问题 6：自动识别失败

**现象**：终端显示 `自动识别失败`（`DISCOVERY_FAILED`）。

**可能原因**
1. 网站结构特殊，接口无法自动发现
2. 页面需要浏览器渲染或登录上下文
3. 网站改版了

**解决**

这**不代表程序损坏**。按顺序尝试：

1. 直接重试一次（网络抖动很常见）
2. 程序自动使用内置浏览器兜底，耐心等待
3. 仍失败时按提示使用 cURL 兜底 → 见 [Manual cURL 指南](MANUAL_CURL.md)

### 问题 7：网站打开正常，但程序找不到岗位

**现象**：浏览器里岗位列表显示正常，程序却抓不到。

**可能原因**
1. 岗位列表是动态加载的，初始 HTML 里没有
2. 岗位数据接口结构特殊
3. 网站结构发生变化

**解决**

重新运行一次；若仍失败，走 cURL 兜底（见 [Manual cURL 指南](MANUAL_CURL.md)）。

## 抓取结果

### 问题 8：岗位数量和官网不一致

**现象**：抓到的岗位数多于或少于官网显示。

**可能原因**
1. 官网页面默认带筛选条件（如只显示"校园"或某城市）
2. 重复岗位被去重
3. 网站接口单次返回有条数限制
4. 翻页未覆盖全部数据

**解决**

- 运行时用 `--scope campus` / `--scope social` 对齐你想要的招聘范围
- 查看 `report.md` 里的去重统计（`duplicate` 相关数字）
- 程序不承诺与官网显示数量完全一致；差距明显时优先检查筛选范围

### 问题 9：JD 不完整 / 没有 JD

**现象**：`jobs.json` 里部分岗位 `full_jd` 为空，或只有摘要。

**可能原因**
1. 列表接口只返回摘要，完整 JD 需要单独的详情接口
2. 详情接口未被自动发现
3. 部分岗位详情获取失败
4. 你选择了"仅保存当前岗位 List"（LIST_ONLY）模式

**解决**

重跑一次；若依旧，按 [Manual cURL 指南](MANUAL_CURL.md) 补充 Detail cURL。`report.md` 的 Data Quality 一节会注明缺失数量。

### 问题 10：`output/_unknown/` 里出现了结果

**现象**：结果保存在 `_unknown` 目录而不是公司名目录。

**可能原因**

公司名无法从页面可靠识别（页面没有明确公司名、或识别出的是域名样式文本），采集本身是**成功**的。

**解决**

打开里面的 `jobs.json` / `report.md` 核对内容是否正确。这不是抓取失败，只是目录没法用公司名命名。

### 问题 11：`output/_failed/` 里出现了内容

**现象**：`_failed` 目录下出现本次运行的目录。

**可能原因**

本次运行未能正常完成（列表抓取失败、接口报错等）。

**解决**

打开其中的 `error_report.json`——它记录了失败的环节和原因码，按提示对照本文档处理；解决后重新运行即可。

### 问题 12：`output/` 没有生成结果

**现象**：运行结束后 `output/` 里找不到本次结果。

**可能原因**
1. 运行被 Ctrl+C 中断，未走到保存阶段
2. 运行失败，结果在 `_failed/` 里
3. 不在项目根目录运行，写到了别处

**解决**

- 确认程序显示过"保存结果"阶段完成
- 检查 `output/_failed/`
- 始终在项目根目录运行 `python main.py ...`

### 问题 13：网站返回 401 / 403

**现象**：提示 `访问被拒绝` 或 HTTP 401 / 403。

**可能原因**

网站拒绝了当前请求，或会话/认证上下文已失效。

**解决**

- 重新打开招聘网站页面，重新运行
- 若使用 cURL 兜底：重新复制**新的** cURL 并立即使用
- 该站点可能需要登录才能访问——请勿尝试绕过登录、验证码或访问控制

## Manual cURL

### 问题 14：cURL 粘贴后解析失败

**现象**：提示 `cURL 解析失败` 或 `UNSUPPORTED_CURL_OPTION`。

**可能原因**
1. 复制的是 `(cmd)` 格式——它用 `^` 续行，程序无法解析
2. cURL 复制不完整，缺了头或尾
3. 含程序不支持的 curl 选项

**解决**

- 在 DevTools 里改用 **Copy → Copy as cURL (bash)** 重新复制
- 确认从 `curl` 开头到最后一行 `-H '...'` 完整复制，再整体粘贴

### 问题 15：输入 END 没反应

**现象**：粘贴完后程序一直在等。

**可能原因**

`END` 没有单独占一行（比如拼在了 cURL 最后一行后面），或大小写输入法处于全角状态。

**解决**

新起一行，只输入半角 `END` 三个字母，回车。想放弃就单独一行输入 `CANCEL`。

### 问题 16：找不到 Detail cURL

**现象**：详情页点击后 Network 里找不到包含完整 JD 的请求。

**可能原因**

详情页是整页跳转，不是接口加载。

**解决**

不需要 Detail cURL 也能完成任务：程序会先尝试自动获取 JD；全部失败时会出现菜单，选 `2. 仅保存当前岗位 List` 即可合法完成（JD 为空但列表完整保存）。详见 [Manual cURL 指南](MANUAL_CURL.md)。

## 其他

### 问题 17：Ctrl+C 之后会发生什么

**现象**：想中途停止运行。

**说明**

按 `Ctrl + C` 即可中断当前运行，程序会显示中断信息并退出。**没有自动保存**：已抓数据是否落盘取决于中断发生的阶段，中断后的目录可能不存在或不完整。

### 问题 18：CSV / XLSX 打不开或乱码

**现象**：Excel 打开 `jobs.csv` 中文乱码，或 `jobs.xlsx` 打不开。

**可能原因**
1. CSV 已按 UTF-8（带 BOM）导出，个别旧版本 Office 仍识别异常
2. xlsx 正被其他程序占用，或文件未下载完整

**解决**

- CSV：用 WPS / 新版 Excel / Numbers 打开；或改用 `jobs.json` / `report.md`
- XLSX：由 openpyxl 标准生成（含 Summary / Jobs / Data Quality 三个工作表），关闭占用它的程序后重新打开

### 问题 19：Windows 路径 / 权限问题

**现象**：克隆、安装或写 output 时报权限错误。

**可能原因**：项目放在系统保护目录（如 `C:\Program Files`）下。

**解决**

- 把项目克隆到普通用户目录（如 `C:\Users\你的用户名\` 或 `D:\`）
- 不要用"以管理员身份运行"作为首选方案

### 问题 20：Mac 权限问题

**现象**：运行时报 `Permission denied`。

**可能原因**：虚拟环境未激活导致 pip 往系统目录写；或项目目录权限受限。

**解决**

- 确认提示符前有 `(.venv)` 再安装依赖
- 不要用 `sudo` 运行本工具；把项目放在个人目录下（如 `~/Coding/`）

### 问题 21：网络超时

**现象**：提示连接超时或长时间无进展。

**可能原因**：网站响应慢、网络不稳定。

**解决**：稍后重试即可。程序对浏览器和详情请求都有内部超时保护，不会永久卡住；如网站长期无法访问，换时段再试。

### 问题 22：公司名识别错误

**现象**：结果目录的公司名与实际公司不符。

**可能原因**：公司名来自页面自动识别，个别网站命名不规范。

**解决**

结果仍然有效，文件内容不受目录名影响；可以自行重命名目录。识别不可靠时程序会直接放入 `_unknown/`（见问题 10）。

### 问题 23：通用引擎不支持某个网站

**现象**：自动模式与 cURL 兜底都无法完成抓取。

**可能原因**：本工具不保证支持所有招聘网站。

**解决**

- 使用 [Manual cURL 指南](MANUAL_CURL.md) 手动兜底
- 或到 GitHub 提 Issue（注意脱敏，见下节）

## 提 Issue 前请检查

**绝对不要**在 Issue、截图或日志里提交：

- `Cookie`
- `Token`
- `Authorization`
- `Session`
- `Signature`
- 私人招聘数据
- 带认证信息的 cURL

需要提供日志或 cURL 时，先删除 / 替换所有敏感请求头（参考 [Manual cURL 指南](MANUAL_CURL.md) 的脱敏清单）。

安全相关的详细说明见 [SECURITY.md](../SECURITY.md)（待添加）。
