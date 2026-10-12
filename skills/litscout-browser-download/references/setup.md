# 安装、配对和人工处理

0.1.1 Skill Release 自带公开版 companion 资源。Agent 运行 `python <Skill目录>/scripts/setup.py` 即可配置独立运行环境、后台启动服务并返回配对码，无需把服务安装步骤交给用户。需要 Python 3.11+，首次依赖安装需联网。

源码目录的一键安装命令是 `python scripts/install.py`，会同时安装专属 Skill、准备固定插件目录并启动服务。已安装 Skill 的 setup.py 不必重复安装自身。返回 JSON 的 status 必须为 ready；code 是原始配对码，供 0.1.0 插件使用，connectionCode 含端口，供 0.1.1+ 插件使用。只把需要的配对码、有效期和插件启用步骤交给用户。

升级后浏览器仍显示旧版时，先查看扩展管理页的实际加载路径。用户可能加载的是早期手动解压目录，而不是安装器返回的 extensionPath。不要直接删扩展或要求重新配对；确认属于本下载版后备份并更新原加载目录，保持扩展 ID，再请用户重新加载。安装输出中的 serviceVersion 是实际运行版本，version 是准备好的版本；复用旧服务不代表它已升级，不应混淆。

默认端口 57133，使用 `.litscout-download` 数据；可选端口 1024–65535，数据目录不得复用 `.litscout-fulltext`。重复运行复用已认证服务，本下载版 Skill/插件在被更新前备份到本版 backups 目录；不修改发现版或全文版。服务没有注册开机启动，电脑重启后再运行 setup.py 恢复。高级参数：`--port`、`--data-dir`；源码安装还支持 `--skills-root`。若手动指定数据目录，业务客户端使用相同 DATA_DIR。

新插件仅需粘贴 connectionCode 即可同步端口。首次浏览器启用仍需用户加载返回的 extensionPath，Agent 不能通过企业策略绕过确认。已安装旧 0.1.0 插件且默认端口时直接用 code，无需为了配对立即替换插件。以下手动说明用于故障排查。

## 安装本机服务

需要 Python 3.11+。在解压后的源码根目录执行：

```powershell
py -3.11 -m venv .venv
& ".venv/Scripts/python.exe" -m pip install .
& ".venv/Scripts/python.exe" -m litscout_download.cli serve
```

服务只监听 `127.0.0.1:57133`，关闭终端会停服。无需管理员权限、Docker 或云端密钥。Linux/macOS 可使用 `python3 -m venv .venv` 和 `.venv/bin/python`，但本版发布前只在 Windows 做了完整浏览器验收。

需要隔离多个实例时显式设置：

```powershell
$env:LITSCOUT_DOWNLOAD_DATA_DIR = "D:/Research/litscout-download-data"
$env:LITSCOUT_DOWNLOAD_API_BASE_URL = "http://127.0.0.1:57143"
& ".venv/Scripts/python.exe" -m litscout_download.cli --port 57143 serve
```

手动启动方式需在运行 Skill 的进程中设置同样的两个变量，插件填写 `http://127.0.0.1:57143`。自动安装的 --port 会写 client.json，不需要地址变量。环境变量仅用于当前进程示例，不要求永久修改系统配置。自动读取浏览器保存文件的目录可用 `LITSCOUT_DOWNLOAD_BROWSER_DOWNLOAD_DIR` 设置，必须是专用本机目录，不能是磁盘根目录或整个用户主目录。

## 安装与配对插件

1. 解压 extension ZIP，打开 `edge://extensions`（Chrome 用 `chrome://extensions`），开启开发人员模式，加载包含 manifest.json 的文件夹。
2. 运行本 Skill 的 `scripts/fulltext.py pair-code`，把输出 code 填入插件侧栏。配对只绑定当前扩展及浏览器 Profile；同机其他 Profile 需要另配。
3. 侧栏显示浏览器 UUID 后，用 `browsers` 核对，试点“测试提醒”。通知失败时检查系统/浏览器通知设置，角标和任务侧栏仍可用。
4. 不把 agent-token、browserToken 或配对码放进论文 JSON、聊天示例和版本库。

加载解压缩扩展时，文件夹路径变化可能导致扩展身份变化；更新时保留原文件夹，在管理页重新加载。若身份改变，重新配对公开版即可，不要挪用旧全文版配对。不要将整套浏览器 Profile 分享给别人。

## 人工处理

| 侧栏状态 | 操作 |
| --- | --- |
| 等待人机验证 / 登录 | 在原文章标签页完成验证，然后点“验证完成，继续” |
| 未识别 PDF 入口 | 点“监听手动下载”，在原文章页点击 PDF；监听 90 秒 |
| 浏览器保存弹窗 | 等文件保存完成；在允许目录内可自动回传，否则“导入已下载 PDF” |
| 文献身份待确认 | 预览核对题名、DOI；只有 inconclusive 才可由用户确认 |
| 文献身份不匹配 | 不提供强行批准；重新获取或导入正确文件，无法取得则跳过 |
| 已跳过但想重试 | 切换“全部”，点“重新获取 PDF”；会清除该条当前指针并打开原入口，旧文件留作排查 |
| 本机已保存 | 服务已保存，但尚未导出；让 Agent 执行 export |
| 已导出到工作目录 | 可查看、复制实际路径，用户与 Agent 读取同一个文件 |

暂停是按任务生效，已开始的持久上传允许完成核验，但不会继续下一篇。验证码待办不会因为 Agent 发送任务级 resume 就自动跳过。一个浏览器严格处理队首，后面的任务会显示被哪个任务阻塞。

## 断线与文件排查

服务停机后重新用相同数据目录启动。未过期租约可继续使用，过期租约进入 `lease_expired` 待人工继续；不要删除数据库或反复创建新任务。缓存只有服务确认接收并解析后才清除。长时间掉线后需检查侧栏并继续，不能保证无人干预恢复所有出版社会话。

清单/事件在 `DATA_DIR/state.sqlite3`；核验文件在 `DATA_DIR/artifacts`；导出文件在 Agent 指定工作目录。服务数据不会自动过期。完整备份要停服后复制整个 DATA_DIR，包含数据库、令牌和 artifacts，备份本身也含敏感信息，应仅交给本人保管。

导出遇同名不同内容文件会报冲突并保留原文件。不要删掉用户已有文件来绕过检查。已导出的文件若被用户移动，侧栏会回落到“本机已保存”；可再导出并更新回执。
