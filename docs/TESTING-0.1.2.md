# 0.1.2 发布验收

验证平台：Windows、Edge、Python 3.11。日期：2026-10-12。

## 自动化回归

- Python：76 项通过，包含非法输入、认证隔离、清单幂等、队列、核验、导出冲突、端口校验及 PDF 字体编码修复。存在一条 Starlette 测试客户端弃用提示，不影响断言。
- Node：56 项通过，包含公开 PDF 无 Cookie 获取、大小限制、HTML 拒绝、暂停中止、arXiv 无后缀地址、下载捕获和通知。
- 真实 Chromium 隔离测试：新增入口回归场景通过。无 CDK 且 LitScout 云端 DNS 不可达时，20 篇不同的合成 PDF 均核验、导出成功；覆盖公开尝试失败回到 DOI 文章页、重新获取不重复公开尝试、首次附件响应捕获、人工暂停、身份确认、手动导入、保存事件、通知、重启恢复和重复提交。
- Skill 结构校验通过。合成 PDF 用于可复现行为测试，不计入下述真实论文。
- 独立 Skill ZIP 在全新临时目录创建 venv、准备扩展、后台启动、配对和客户端连接，9 项检查通过；源码 ZIP 解压后的启动与 API 冒烟测试 6 项通过。均未读取 CDK。

## 真实 Edge / Skill 测试

Codex 自行在 arXiv 官方检索页搜索，再读取论文摘要页的 citation 元数据。未使用 LitScout 云端、CDK 或发现版 Skill。下载由已配对的 Edge 扩展完成，Agent 通过已安装的下载专用 Skill 提交、查询和导出。

最终一轮五篇全部产生 `public_pdf_captured` 事件，均为 `verified`，无需人工批准，成功导出 5/5。逐文件重新计算 SHA-256，与 manifest 一致，并调用正文接口检查第一页和题名匹配结果。

| 论文 | 官方记录 | 页数 | 文本状态 |
| --- | --- | ---: | --- |
| Fourier Neural Operator for Parametric Partial Differential Equations | https://arxiv.org/abs/2010.08895 | 16 | text_available |
| Physics-Informed Neural Operator for Learning Partial Differential Equations | https://arxiv.org/abs/2111.03794 | 27 | text_available |
| Fourier Neural Operator with Learned Deformations for PDEs on General Geometries | https://arxiv.org/abs/2207.05209 | 26 | text_available |
| Operator Learning Enhanced Physics-informed Neural Networks for Solving Partial Differential Equations Characterized by Sharp Solutions | https://arxiv.org/abs/2310.19590 | 27 | partial_text；第 7、8 页字体映射有非法字符 |
| MgFNO: Multi-grid Architecture Fourier Neural Operator for Parametric Partial Differential Equations | https://arxiv.org/abs/2407.08615 | 31 | text_available |

## 实测发现与修复

1. arXiv 的 `/pdf/<id>` 不以 `.pdf` 结尾，旧插件会误报未发现 PDF。新版识别该路径及页面 PDF MIME，监听后重载已打开的 PDF。
2. 首次处理先请求清单中的公开 `pdfUrl`，不带 Cookie、不跟随重定向，最多 20 秒；失败从 DOI 或文章页进入浏览器处理，公开字节仍需本机核验。重新获取不重复公开请求。
3. OL-PINN 的 PDF 能打开，但提取文本包含孤立 Unicode 代理字符，旧解析器写 UTF-8 JSON 时失败。新版合并合法代理对，对非法字符记录受影响页码并标记 partial_text，不修改原 PDF。
4. 删除针对某个本机端口的禁用规则，保留回环地址、范围、服务身份和令牌校验。

未把用户 PDF、配对、令牌、任务数据库或本机路径放入发布包。五篇成功不代表所有出版社均可自动下载；授权验证和无法确定身份的文件仍需人工处理。
