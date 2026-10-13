# LitScout Download · 文献批量下载

当前版本：**0.1.4**。Agent 可以安装 Skill、准备插件目录、后台启动本机服务并返回配对码。

**复制这一句话给 Codex：**

> 请从 https://github.com/yyy-OPS/litscout-download 安装最新稳定版 LitScout Download，按 README 自动安装下载专用 Skill、准备浏览器插件并启动本机服务，保留已有服务和下载记录，告诉我插件加载目录并返回配对码，连接后帮我将选定论文下载、核验并导出到指定工作目录。

[GitHub 仓库](https://github.com/yyy-OPS/litscout-download) · [最新 Release / 下载包](https://github.com/yyy-OPS/litscout-download/releases/latest) · [问题反馈](https://github.com/yyy-OPS/litscout-download/issues)

Agent 负责下载和配置，首次启用扩展仍需你在浏览器中点击“加载解压缩的扩展”，然后粘贴配对码。需要 Python 3.11+ 和本机命令执行能力。

把 Agent 找到的论文清单发送到自己的 Edge / Chrome，依次获取有权访问的 PDF，核验后导出到研究工作目录。

这是 LitScout 系列的独立下载版。无需 CDK、无需 LitScout 账号，也不调用 LitScout 云端。Agent 可以使用自己的搜索工具整理清单。官网：[litscout.yangy.cn](http://litscout.yangy.cn/)。插件左上角品牌链接仅在点击时打开官网，不附带任务或文献信息。

使用前请确认你有权访问相关文献，并遵守出版社、机构网络和其他来源的使用规则。批量或高频下载可能触发访问限制，导致 IP 临时或长期封禁。请按实际需要控制批次和请求频率；因使用本项目造成的访问中断、IP 封禁或其他损失，开发者不承担责任。

## 能做什么

- 每次提交 1–20 篇论文；同一浏览器顺序下载，最多保留 100 篇未完成队列。
- 提供 `pdfUrl` 时，首次处理先尝试无 Cookie 的公开 PDF 获取。未成功则从 DOI 文章页进入浏览器流程；无 DOI 时用文章页，只有 PDF 链接时才直接打开 PDF。公开尝试不改变文章入口，重新获取从 DOI 或文章页开始，不重复公开尝试。支持 arXiv 无 `.pdf` 后缀的地址；仅 DOI 不会自动查询 OA 数据库，Agent 应自行补充可验证的公开链接。
- 捕获 PDF 页面、支持的阅读器响应及与当前论文关联的浏览器下载。
- 人机验证或登录时等待用户；支持暂停、继续、跳过、重新获取和手动导入。
- 接单及需要人工处理时发送浏览器通知，并显示角标；系统免打扰可能隐藏桌面弹窗。
- 侧栏按任务切换，查看/复制原论文清单、预览文件、查看保存及导出位置。
- 本机校验 PDF 格式、题名/DOI，生成 SHA-256 和分页文本；核验通过后导出到 Agent 指定目录。
- 本机 SQLite 保存清单、任务、事件和导出回执；重启后保留配对和记录。

不提供文献搜索、云端质量评价、登录代办、验证码绕过或无权限下载。扫描 PDF 可以保存，但不包含 OCR 引擎。全文身份核验是保守的文本规则，不能代替逐篇研究判断。

## 一键安装：交给 Agent 处理

将本仓库或源码 Release 的链接交给 Codex / Claude Code / 其他能运行本机命令的 Agent，发送：

> 按本页顶部的一句话安装指令操作；检查源码后运行 scripts/install.py，返回连接码和插件目录，不要打印本机长期令牌。

在解压后的源码根目录，安装命令只有一条：

```powershell
python scripts/install.py
```

Windows 有 Python Launcher 时也可以使用 `py -3.11 scripts/install.py`；macOS/Linux 使用 `python3 scripts/install.py`。前提是主机有 Python 3.11+，且首次安装能访问 Python 包源。脚本不全局安装依赖、不修改系统环境变量、不配置开机启动。

这条命令会：

1. 将专属 Skill 安装到 `$CODEX_HOME/skills/litscout-browser-download` 或 `~/.codex/skills/litscout-browser-download`；旧文件先备份。
2. 将插件放到固定目录 `~/.litscout-download/extension`，后续更新保持路径。
3. 创建版本隔离的 Python venv，安装服务所需依赖。
4. 后台启动回环服务并验证本机令牌；已有可用服务则复用。
5. 返回 `status=ready`、`code`、`connectionCode`、服务地址和插件目录。除短期配对码外不返回令牌。

已有 0.1.0 插件：默认地址 57133 下直接粘贴 `code`。0.1.1+ 插件：粘贴 `connectionCode`，即使使用自定义端口也会自动设置地址。码有效 5 分钟，过期让 Agent 重新生成。

首次安装扩展时，仍需用户在 `edge://extensions` 开启开发者模式、点击“加载解压缩的扩展”，选择返回的 `extensionPath`。这是浏览器的确认步骤，安装器不会修改浏览器 Profile、企业策略或静默启用扩展。已经启用的用户无需重复安装；更新文件后在管理页点“重新加载”。

## 只安装 Skill 包也可以

将 Release 中的 `litscout-browser-download-0.1.4-skill.zip` 解压到 Codex skills 目录。包内包含安装助手和公开版 companion 资源，不需要再下载源码包。让 Agent 执行：

```powershell
python "<Skill目录>/scripts/setup.py"
```

例如对 Agent 说：

> 使用 `$litscout-browser-download`，自动配置下载服务并把配对码发给我。我已安装 Edge 插件。

setup.py 建立运行环境后会后台启动服务，关闭执行它的终端不影响已启动服务。电脑重启后再次运行该命令即可恢复，配对与记录仍保留。它不是 Windows 服务或开机自启动任务。

脚本重复运行不会重新配对或清空数据。已配对时 Agent 可直接调用业务命令。端口冲突会明确报错，不终止其他程序；可指定独立端口：

```powershell
python scripts/install.py --port 57143
```

地址保存在本机 `client.json`，新插件使用返回的 connectionCode 自动切换端口。可用端口范围为 1024–65535，没有针对某个端口的禁用规则。安装器会验证占用该端口的服务身份和本机令牌，不能复用时报告冲突。若服务本身采用自定义数据目录，Agent 后续命令需要设置对应的 `LITSCOUT_DOWNLOAD_DATA_DIR`；默认安装不需要任何变量。

## 从 GitHub 安装

仓库：<https://github.com/yyy-OPS/litscout-download>。Agent 可下载 [最新 Release](https://github.com/yyy-OPS/litscout-download/releases/latest) 的源码 ZIP，并核对同一 Release 的 SHA256SUMS，再解压运行安装器。这样不需要 Git。

装有 Git 时，可在一个新的空目录中运行：

```powershell
git clone --branch v0.1.4 --depth 1 https://github.com/yyy-OPS/litscout-download.git
cd litscout-download
python scripts/install.py
```

用户可让 Agent 下载 GitHub Release 的源码 ZIP、核对同一 Release 的 SHA256SUMS，解压后运行 `python scripts/install.py`；也可从目标仓库克隆后运行同一命令。只安装 Skill 的 Agent 可使用 Skill ZIP 中的 setup.py。发布包分别提供源码、扩展和含 companion 的 Skill，使用 MIT 许可。

## 手动安装与排障

需要 Python 3.11+、Edge 或 Chromium 120+。Windows 是本次验证平台。服务与浏览器必须在同一台主机上；WSL、远程容器和云端 Codex 不能直接复用主机回环地址。

在源码目录执行（PowerShell）：

```powershell
py -3.11 -m venv .venv
& ".venv/Scripts/python.exe" -m pip install .
& ".venv/Scripts/python.exe" -m litscout_download.cli serve
```

保持服务终端运行。默认地址 `http://127.0.0.1:57133`，默认数据目录为用户主目录下的 `.litscout-download`。首次启动自动生成本机 Agent 令牌，不需要手工填写云端密钥。

1. 打开 `edge://extensions`，开启开发人员模式，选择“加载解压缩的扩展”，选中 `apps/browser-extension`（Release 插件 ZIP 先解压）。
2. 在另一个终端执行：

   ```powershell
   & ".venv/Scripts/python.exe" "skills/litscout-browser-download/scripts/fulltext.py" pair-code
   ```

3. 点击插件图标打开右侧栏，填写服务地址和输出的一次性配对码；配对码有效期 5 分钟，仅用一次。
4. 将 `skills/litscout-browser-download` 文件夹复制到自己的 Codex skills 目录（通常为 `~/.codex/skills`），或让其他 Agent 阅读其中的 `SKILL.md`。已有同名目录时先备份、比较，不直接覆盖。
5. 执行下述清单示例，或对 Agent 说：“把这些论文交给 LitScout Download 下载，导出到我的研究目录，遇验证提醒我。”

上述手动方式不会安装 Skill；自动安装请使用 install.py。本版默认 `57133`，数据目录独立。原 `litscout`、`litscout-fulltext` Skill 不会被替换。

## 提交清单

保存 `papers.json`。最少提供题名和 DOI 或 HTTPS 链接；这里的论文是真实可识别的示例，是否能下载取决于访问情况。

```json
{
  "schemaVersion": 1,
  "listId": "my-reading-list-001",
  "taskName": "Transformer 阅读清单",
  "papers": [
    {
      "id": "attention-2017",
      "title": "Attention Is All You Need",
      "identifiers": {"arxivId": "1706.03762"},
      "links": {
        "landingPage": "https://arxiv.org/abs/1706.03762",
        "pdfUrl": "https://arxiv.org/pdf/1706.03762"
      },
      "year": 2017
    }
  ]
}
```

```powershell
# browsers 返回数组，取已配对项的 id
& ".venv/Scripts/python.exe" "skills/litscout-browser-download/scripts/fulltext.py" browsers
& ".venv/Scripts/python.exe" "skills/litscout-browser-download/scripts/fulltext.py" submit --list "papers.json" --browser "浏览器UUID" --key "reading-list-001-run-1"
# 使用返回的 taskId；输出目录必须是本机绝对路径
& ".venv/Scripts/python.exe" "skills/litscout-browser-download/scripts/fulltext.py" wait "任务UUID" --output-dir "D:/Research/transformer/papers"
& ".venv/Scripts/python.exe" "skills/litscout-browser-download/scripts/fulltext.py" export "任务UUID" --output-dir "D:/Research/transformer/papers"
```

`wait` 最多等待 300 秒；遇人工待办、暂停或终态会返回，已完成文献可先导出。它返回并不代表全部成功，应读取 `status`、`progress`、`attention` 和 `workspaceExport.errors`。同一提交重试复用 `--key`；换 key 代表明确创建另一轮下载。

## 配置及文件位置

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `LITSCOUT_DOWNLOAD_DATA_DIR` | `~/.litscout-download` | 服务与 Skill 共同使用的数据目录 |
| `LITSCOUT_DOWNLOAD_API_BASE_URL` | client.json 的地址，缺省 `http://127.0.0.1:57133` | 覆盖 Skill 访问本机服务的地址 |
| `LITSCOUT_DOWNLOAD_BROWSER_DOWNLOAD_DIR` | 系统 Downloads 目录 | 已保存 PDF 自动回传时允许读取的目录 |
| 服务 `--port` | `57133` | 监听端口，只绑定 `127.0.0.1` |
| 服务 `--data-dir` | 同 DATA_DIR | 覆盖服务数据目录，客户端也需配置同一路径 |
| 客户端 `--output-dir` | 无，显式指定 | 核验后的工作目录，绝对本机路径 |

默认自动安装无需配置环境变量；不读取 `.env` 文件。安装器 `--port` 会更新 client.json，并生成包含端口的连接码；手动启动服务改端口时需自行同步客户端和插件地址。令牌由本机生成，保存在数据目录的 `agent-token`；不要提交到 GitHub 或发送给他人。setup.log 记录安装过程，service.log 记录后台服务错误，runtime 保存独立环境，backups 保存被更新的 Skill/插件文件。

PDF 持久保存在 `DATA_DIR/artifacts/capture-*/paper.pdf`，旁边 `text.json` 保存核验、哈希和文本。导出会再写入指定工作目录，并生成 `litscout-<taskId>-manifest.json`。本版不会自动清理历史或缓存；备份前停止服务，将整个数据目录一起备份。单 PDF 限制 32 MiB，本机 artifacts 预算 1 GiB，空间不足会停止接收并报告错误。

PDF 中的字体编码可能不完整。可还原的 Unicode 代理对会转换为对应字符，无法还原的字符显示为 `�`，受影响的页码通过 `invalidUnicodePages` 返回，正文标记 `partial_text`。原 PDF 字节保持原样；分析公式时请核对对应页面。`ocr_required` 表示正文文字不足，需要另行 OCR。

如果浏览器另存到允许下载目录之外，自动回传会被拒绝，此时在侧栏使用“导入已下载 PDF”。浏览器保存弹窗出现不等于文件完成写入，也不等于身份核验通过。

## 文档与测试

- [架构与拆分边界](docs/ARCHITECTURE.md)
- [Agent API 与清单协议](skills/litscout-browser-download/references/api.md)
- [人工处理、安装和排障](skills/litscout-browser-download/references/setup.md)
- [隐私与浏览器权限](docs/PRIVACY.md)
- [自动配置设计](docs/AUTO_SETUP.md)
- [发布与商城准备](docs/RELEASE.md)
- [第三方依赖说明](THIRD_PARTY_NOTICES.md)

```powershell
& ".venv/Scripts/python.exe" -m pip install -e ".[test]"
& ".venv/Scripts/python.exe" -m pytest -q
node --test apps/browser-extension/tests/*.test.js
& ".venv/Scripts/python.exe" -m playwright install chromium
& ".venv/Scripts/python.exe" -X utf8 "scripts/browser_smoke.py"
```

## License

项目代码、插件和 Skill 使用 MIT。第三方组件保留各自许可证。私有 LitScout 云端实现、CDK、真实配对、用户检索记录和下载论文不属于本仓库。

LitScout Download is a local, cloud-independent paper download companion for AI agents and paired browsers. Submit a paper list, handle access challenges in your browser, and export verified PDFs to your workspace. No CDK or cloud account is required. See the linked protocol and setup guides for the API and operational limits.
