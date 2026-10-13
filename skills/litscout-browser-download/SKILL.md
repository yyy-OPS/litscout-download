---
name: litscout-browser-download
description: Set up LitScout Download locally and return a browser pairing code, then batch-download selected papers through Edge or Chrome, handle human verification, and export verified PDFs to a research workspace. No cloud credentials required.
metadata:
  version: 0.1.4
---

# LitScout Download · 浏览器文献下载

用于已有论文清单的全文获取。用户或 Agent 自行检索并提供真实题名、DOI、文章或 PDF 链接；本 Skill 不搜索文献、不调用 LitScout 云端、不读取 `LITSCOUT_CDK`。仅使用用户有权访问的公开或机构全文。

首次安装、连接失败或人工处理时读 [setup.md](references/setup.md)；构造输入、查询状态或排查错误时读 [api.md](references/api.md)。Skill Release 内含公开版服务和插件安装资源；首次安装依赖需要网络，不需要 CDK。

## 自动准备与配对

用户要求安装、连接或使用下载版时，由 Agent 执行 `python <本Skill目录>/scripts/setup.py`。它会建立独立 venv、后台启动服务、验证本机身份并返回 JSON。不要把启动服务、配置环境变量和生成配对码的命令直接丢给用户执行。若已有有效配对且 `browsers` 可访问，可直接使用，不必重新生成配对。

首次依赖安装可能需要几分钟：用非阻塞命令运行，持续读取进度并向用户说明。安装器将日志写入返回数据目录下的 setup.log/service.log。只有 `status=ready` 才能声称服务就绪；错误时先看原因，不能伪造 code。

把返回的 `code` 原样发给已安装 0.1.0 插件的用户（默认 57133）；0.1.1+ 插件推荐返回 `connectionCode`，其中包含端口，用户只需粘贴到配对码框。说明有效期 5 分钟，过期再运行 setup.py 生成新码，不修改其他配对。永远不显示 agent-token 或 browserToken。插件还未启用时，说明 edge://extensions 中“加载解压缩的扩展”并给出 extensionPath；首次确认必须由用户完成，不能宣称已静默安装。

默认使用 57133 和 `.litscout-download`。允许 1024–65535 范围内的端口，不针对某个端口设禁用规则。端口被其他服务占用时不结束它，检查已保存 client.json 或选择空闲端口并使用连接码；已有服务必须通过服务名及本机令牌核验才能复用。数据目录必须独立，不能复用旧全文版目录。重启电脑后再次运行 setup.py 恢复服务，现有配对保留。无需修改系统环境变量或注册自启动任务。

## 执行

1. 确定本机绝对工作目录，将原论文清单 JSON 存入该目录。每批 1–20 篇；至少有题名和 DOI 或公开 HTTPS 链接。DOI、题名、链接必须来自实际检索结果，不能猜测。清单内去重；跨批保留自己的总清单。
   若检索来源提供公开 PDF 直链，将其放入 `links.pdfUrl`，文章页仍保留在 `links.landingPage`。首次处理可无 Cookie 尝试公开链接，最多 20 秒；失败后从 DOI 文章页进入浏览器流程，无 DOI 时用文章页，只有 PDF 链接时才直接打开 PDF。重新获取也从 DOI 或文章页开始，不重复公开尝试；继续和手动监听保留用户完成验证的当前标签页。公开候选不能替代文章入口。仅 DOI 输入不能自动发现所有 OA 副本，Agent 应用自己的检索工具补充真实公开链接。此过程不调用 LitScout 云端。
2. 用客户端 `browsers` 查看配对。它返回数组，浏览器 ID 是 `id`，`seen` 为最后心跳秒时间；没有 `online` 字段。只有一个有效配对时可选用它，多配对时按用户意图选择；目标不明确再询问。不要覆盖现有配对。
3. `submit --list <JSON文件> --browser <UUID> --key <稳定幂等键>`。记录返回的 `listId` 和 `taskId`。同一逻辑提交的网络重试必须复用 key；响应丢失不应换 key。
4. 提交成功后立即在聊天中告诉用户任务名、篇数及“请检查 Edge/Chrome 的 LitScout Download 侧栏，出现登录或人机验证时协助处理”。桌面通知由插件提供；不要假定通知一定可见。
5. `wait <taskId> --output-dir <绝对工作目录>` 监控并增量导出。最多等待 300 秒后会返回；继续查询同一任务。`waiting_user` / `paused` 时读取 `attention`，说明当前论文及阻塞原因，让用户操作侧栏。人工完成或跳过当前篇后才轮到下一篇。不得自动批准身份不明的 PDF、解验证码或循环重试付费墙。可使用 `open <taskId> <itemId>` 请求插件打开对应文章，或用 `pause` / `resume` 协助管理任务；任务 resume 不会替用户完成验证码。
6. 完成后再次 `export <taskId> --output-dir <绝对工作目录>`，检查 `exported`、`pending`、`errors`。导出文件在指定目录，manifest 含论文、任务、哈希和核验状态；不要把浏览器缓存或“本机已保存”称为已导出。文件冲突不覆盖，先查明内容或换一个经用户授权的输出目录。
7. 向用户报告成功导出、待人工、失败/跳过的准确数量，并提供工作目录和 manifest 路径。后续全文分析基于已核验的本地 PDF 或 `text` 接口；`ocr_required` / `partial_text` 必须披露，不能据此声称读过全部正文。
   `invalidUnicodePages` 列出字体编码无法完整还原的页码，应查看原 PDF 核对公式和符号；`emptyTextPages` 列出没有提取出正文的页码。文件身份核验通过不等于正文提取无损。

所有业务命令形如 `python <本Skill目录>/scripts/fulltext.py <命令>`。客户端自动读取 `.litscout-download/client.json` 中的服务地址，以及同目录 agent-token。高级配置可用 `LITSCOUT_DOWNLOAD_DATA_DIR` 和 `LITSCOUT_DOWNLOAD_API_BASE_URL` 覆盖，通常不需要。无需向用户索要 CDK。

本版与 `litscout` / `litscout-fulltext` Skill 和其他浏览器扩展分开安装。不复制旧数据、不改其他 Skill、不使用其他服务的令牌补救连接失败。
