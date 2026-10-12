# 本机 Agent API / 清单协议 v1

服务只接受 `http://127.0.0.1:<port>`。Agent 请求携带 `Authorization: Bearer <本机agent-token>`，不得带网页 Origin；网页不能直接调用 Agent 接口。浏览器走独立令牌和扩展 ID 边界。令牌不是 CDK。

安装助手 `scripts/setup.py` 自动配置并启动服务，返回 code/connectionCode；业务客户端自动读取 DATA_DIR/client.json。端口范围为 1024–65535，默认 57133。connectionCode 格式为 `litscout-download:v1:<端口>:<一次性code>`，供 0.1.1+ 插件解析；本机 `/v1/pair` 仍接收原始 code，因此与 0.1.0 插件兼容。

## 创建任务

`POST /v1/tasks`，请求头 `Idempotency-Key` 必填，8–128 字符；成功返回 202 和完整任务快照。字段未知、缺失或不合法返回 422；JSON 请求体最大 64 KiB。

```json
{
  "schemaVersion": 1,
  "browserId": "00000000-0000-0000-0000-000000000000",
  "listId": "review-2026-001",
  "taskName": "工程论文阅读",
  "papers": [{
    "id": "paper-001",
    "title": "Example paper title",
    "identifiers": {"doi": "10.1234/example"},
    "links": {"landingPage": "https://publisher.example/article"},
    "authors": ["Author Name"],
    "year": 2026,
    "venue": "Example Journal"
  }]
}
```

以上是字段示意，不是真论文或可用浏览器 ID。Skill 客户端从 `--browser` 注入真实 UUID，JSON 文件无需填写 browserId。

| 字段 | 要求 / 语义 |
| --- | --- |
| `schemaVersion` | 当前只能是 1，默认 1 |
| `browserId` | 已配对浏览器 UUID；不得假设是扩展 ID |
| `listId` | 可选，1–100 位字母、数字、下划线或连字符；缺省生成 UUID。标识不可变清单 |
| `cloudJobId` | 可选，同 listId 字符规则；仅存储关联值，不鉴权、不拉取云端结果 |
| `taskName` | 可选，最多 120 字符 |
| `papers` | 必填，1–20 项 |
| `paper.id` | 可选，清单内唯一；缺省按顺序生成 `paper-001` 等，规则同 listId |
| `paper.title` | 必填，非空，最多 1000 字符，供用户查看和身份核验 |
| `paper.identifiers` | 可选对象，支持 doi、arxivId、pmid；doi 支持裸值及 doi.org URL，归一为小写裸值 |
| `paper.links` | 支持 landingPage、pdfUrl、doiUrl、arxivUrl、sourceUrls（来源名到 URL 的映射） |
| `authors / year / venue` | 可选，作者名字符串数组 / 1000–2200 年 / 刊名字符串 |

必须至少有 DOI 或一个可访问定位链接；仅提供 arxivId/pmid 不会自动查询出版社。提供 DOI 时补齐 doiUrl。链接只接收 HTTPS 默认 443 端口，无用户名密码，拒绝 IP 字面量和明显本机域名。它不是 DNS/重定向防火墙：实际出版商页面和跳转在用户浏览器中打开，不将未经信任的论文列表当作安全内容。

有 pdfUrl 时，0.1.2+ 插件先以不带 Cookie、不跟随重定向的请求尝试公开 PDF，最多 20 秒；检查文件头和大小后交给本机核验身份。失败后转入配对浏览器页面捕获；未提供 pdfUrl 时按 landingPage、doiUrl、arxivUrl、sourceUrls 选择入口。公开尝试记录 public_pdf_attempt、public_pdf_captured 或 public_pdf_fallback 事件原因。入口失效会交给人工，可打开文章或导入正确文件。输入不支持直接混入云端所有额外字段，Agent 需投影为上述字段；可保留 cloudJobId 追溯来源。

同 key、同归一化请求返回原 taskId；同 key、不同请求返回 `IDEMPOTENCY_CONFLICT`。同 listId 不允许改论文顺序、内容或 cloudJobId，返回 `LIST_ID_CONFLICT`。修改清单请使用新 listId。使用新 key 和原清单可显式创建新一轮任务，适用于重试或另一个配对浏览器。`itemId` 是这轮执行的条目 UUID，与清单内 paper.id 不同。

## Agent 路由

| 方法和路径 | 用途 |
| --- | --- |
| `GET /healthz` | 无令牌的服务名/版本检查 |
| `POST /v1/pair-codes` | 一次性配对码（300 秒） |
| `GET /v1/browsers` | 配对数组：id、extension、name、seen、revoked |
| `POST /v1/browsers/{id}/revoke` | 撤销该浏览器并终止旧租约 |
| `POST /v1/tasks` | 提交本地论文列表 |
| `GET /v1/lists/{listId}` | 原清单及可选 cloudJobId |
| `GET /v1/tasks/{taskId}` | 任务、文献、注意事项及保存/导出状态 |
| `GET /v1/tasks/{taskId}/events?after=0` | 增量事件数组，最多 200 项；继续传最后 seq |
| `POST /v1/tasks/{taskId}/action` | `{"action":"pause"}`、resume 或 rename（附 name） |
| `POST /v1/tasks/{taskId}/cancel` | 取消任务未完成项，不删除已保存文件 |
| `POST /v1/tasks/{taskId}/items/{itemId}/open` | 返回 commandId，请求配对浏览器打开文章 |
| `GET .../items/{itemId}/artifact` | 仅身份已通过的 PDF |
| `GET .../items/{itemId}/text?start=1&count=10` | 分页文本、页数、哈希、核验，count 最大 20 |
| `POST .../items/{itemId}/export` | 导出回执：outputPath、sha256、bytes；服务重验本机文件 |

浏览器的配对、领取、上传、手动批准等 `/v1/browser/*` 接口仅供配对插件，不应把浏览器令牌交给 Agent 来替用户批准文件。

## 状态与错误

任务 `status`：running、waiting_user、paused、completed、partial、failed、cancelled。`progress` 分别记录 total、downloaded、readable、exported、finished；downloaded 可包含身份不明文件，不能等同 readable。

文献基本流程：needs_browser → browser_running → uploading → completed。登录/验证码进入 waiting_user；身份不确定进入 verification_required。明确 mismatch 不允许批准，应重新获取、导入正确文件或跳过。inconclusive 可以预览后由用户确认，核验状态变为 user_approved。错误文件不会交给 Agent 正文接口。

| 错误 / HTTP | 处理 |
| --- | --- |
| AGENT_UNAUTHORIZED / 401 | 检查客户端和服务数据目录是否相同 |
| BROWSER_UNAUTHORIZED / 401 | 配对失效或已撤销，检查指定的插件 |
| HOST_BLOCKED、ORIGIN_BLOCKED / 403 | 必须使用回环主机；网页不能冒用 Agent |
| BROWSER_NOT_FOUND、LIST_NOT_FOUND / 404 | ID 不存在，先核对目标服务 |
| IDEMPOTENCY_CONFLICT、LIST_ID_CONFLICT / 409 | 不覆盖旧请求，核对清单和 key |
| LEASE_STALE / 409 | 旧上传已失效，查看当前任务并由用户继续 |
| ARTIFACT_NOT_VERIFIED / 409 | 等待身份核验，不读取/导出错误论文 |
| QUEUE_FULL / 429 | 未完成队列达到 100 篇，先处理已有任务 |
| REQUEST_TOO_LARGE、PDF_TOO_LARGE / 413 | JSON 最大 64 KiB、单 PDF 最大 32 MiB |
| PDF_PARSE_FAILED、NOT_A_PDF / 422 | 检查是否下载了网页或损坏 PDF |
| LOCAL_STORAGE_FULL / 507 | artifacts 预算或磁盘空间不足 |
| EXPORT_FILE_CONFLICT（客户端） | 目标文件内容不同，不覆盖，排查或换目录 |

## 本机 Python 示例

以下脚本读取本机令牌，避免在命令历史中填写令牌。服务默认目录不同或已改端口时请设置相应变量。

```python
import json, os
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler

root = Path(os.environ.get("LITSCOUT_DOWNLOAD_DATA_DIR") or Path.home() / ".litscout-download")
saved = json.loads((root / "client.json").read_text()) if (root / "client.json").exists() else {}
base = os.environ.get("LITSCOUT_DOWNLOAD_API_BASE_URL") or saved.get("baseUrl", "http://127.0.0.1:57133")
opener = build_opener(ProxyHandler({}))
request = Request(base + "/v1/browsers", headers={
    "Authorization": "Bearer " + (root / "agent-token").read_text().strip()
})
with opener.open(request, timeout=10) as response:
    print(json.load(response))
```

完整提交、等待、分页文本和无覆盖导出优先使用附带客户端，避免重复实现文件完整性检查。
