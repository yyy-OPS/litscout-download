# 0.1.1 自动配置与发布验收

本页记录历史版本行为。0.1.2 已取消 57123 专项禁用，改用通用端口范围和服务身份校验；见当前版本验收文档。

测试平台为 Windows / Python 3.11，浏览器采用隔离 Chromium Profile。0.1.0 下载流程记录保留在 TESTING.md，本文记录本轮新增能力。

## 自动化检查

- Python：71 项通过。包括原有 59 项下载、清单及导出测试，以及 12 项配置保护、安装锁、端口冲突、错误身份和归档安全检查。
- Node：51 项通过。新增含端口连接码解析、拒绝远程地址及旧 57123 端口测试。
- 独立 Skill 包安装：9 个检查点通过。将 Skill ZIP 解压到临时目录，在没有源码目录的条件下创建新 venv、安装依赖、后台启动、重复配置复用服务、接受真实配对码、业务客户端自动读取非默认端口、准备扩展目录、保留自包含安装资源并拒绝旧端口。
- 浏览器：通过配对码自动设置非默认测试端口，继续执行完整人工处理与 20 篇测试 PDF 下载/核验/导出回归；JSON 检查点记录在 browser-acceptance-0.1.1.json。

测试未使用 CDK，也没有向 LitScout 云端提交检索或下载任务。PDF 为本机生成的不同题名/DOI 文档，不能据此宣称真实出版社的通用下载成功率。

浏览器回归发现：服务刚进入人工待办而 worker 尚在收尾时，立即导入可能被判为忙碌。0.1.1 会为同一篇的收尾等待最多 3 秒，仍有其他下载工作时继续拒绝导入，避免并发归档错篇。

## 复现命令

在已安装测试依赖的源码目录运行：

```powershell
python -m pytest -q
node --test apps/browser-extension/tests/*.test.js
python scripts/package_release.py --output test-output/release-check
python scripts/setup_smoke.py test-output/release-check/litscout-browser-download-0.1.1-skill.zip
python scripts/package_smoke.py test-output/release-check/litscout-download-0.1.1-source.zip
python scripts/browser_smoke.py
```

setup_smoke 会创建并清理自己的临时服务环境，只结束它自己启动的服务。browser_smoke 使用 57139；package_smoke 使用 57149；均不占用旧 57123。可以通过 LITSCOUT_SMOKE_CHROMIUM 指定隔离测试 Chromium 可执行文件。

## 交付边界

安装命令准备浏览器扩展文件，但不静默启用扩展。首次加载仍由用户确认；已加载插件只需粘贴配对码。配对码会过期，测试输出和发布包不包含可用的生产配对码或本机令牌。

依赖下载与后台服务启动有独立日志。安装期间不会修改系统环境变量、浏览器策略或注册开机启动。重复安装保留本版记录；原有发现版、全文版 Skill 和旧服务保持独立。
