# 发布范围与商城准备

## GitHub 与 Release

仅以本独立目录作为 GitHub 仓库根目录。不要在上级工作区初始化公开仓库或推送已有私有仓库历史。准备的源码包不含 `.git` 历史；这个设计比从私有仓库删除文件后公开更容易核查。

`scripts/package_release.py` 使用发布白名单生成源码、扩展、下载 Skill 三个 ZIP 及 SHA256SUMS。Skill 包内含公开服务和插件 companion.zip 及其校验摘要，可以自行配置服务。源码包提供 `python scripts/install.py` 一键安装。当前不包含签名 EXE，不需要部署云端服务。

发布命令为 `python scripts/package_release.py`，输出 `dist/0.1.3`。公开仓库为 <https://github.com/yyy-OPS/litscout-download>。README、MIT LICENSE、完整下载版源码和 Release 资产一起提供。脚本在发现 .git 时要求先单独检查历史，可从审阅过的干净源码副本生成发布包；只允许扫描通过的源码包进入独立公开仓库。

发布前运行自动测试，再运行打包脚本；它检查文件清单、内容、禁止文件和 ZIP CRC，不包含真实文献、SQLite、个人路径、配对令牌、环境密钥和私有云端目录。若未来要提交 GitHub，先在该独立目录检查 `git status` 和暂存差异，确认最终公开文件。自动扫描不代替人工审查。

## Edge Add-ons / Chrome Web Store

首发可先在 GitHub 发布源码、Release 及安装说明。开发者模式加载可以立即供用户测试，商城发布需另走审核。

准备项：开发者账号、商店名称与描述、图标、真实侧栏截图、公开隐私政策链接、支持入口、权限说明。扩展 ZIP 必须以 manifest.json 为根文件。管理员需按商店当时的政策填写隐私披露，并说明插件依赖用户本机安装的 companion 服务，不会在后台部署软件。

`debugger`、广泛 HTTPS 访问、downloads 和 unlimitedStorage 是需要重点解释的权限，不能保证现有权限配置一定通过商店审核。不要为了避免提示而绕过浏览器权限模型。商城版获得固定扩展 ID 后，用户仍需在自己的机器配对，不能预置共享令牌。

参考官方入口（具体要求以提交时页面为准）：

- https://learn.microsoft.com/en-us/microsoft-edge/extensions-chromium/publish/publish-extension
- https://developer.chrome.com/docs/webstore/publish

源码公开后，ZIP、CRX 和商店安装版都可被查看 JavaScript。压缩/混淆不提供保密保证。本版接受下载客户端开源，私有云端代码仍必须独立保管。
