# 验收记录

## npm 分发验收（2026-09-20）

用户已授权公开 npm 包；GitHub 仓库保持私有。npm 包包含完整应用源码及 Python 锁文件，运行环境在首次启动时准备，不是离线自包含二进制。

Windows Node.js 22：4 项 Node 离线测试通过；真实官方 uv 0.12.17 下载、SHA256 校验、解压及版本检查通过。实际 tgz 通过 npx 启动；独立全局安装前缀中的 qagent 安装 89 个锁定依赖后成功启动，中文记忆新增和重启读取通过。打包白名单共 16 个文件，不包含凭据、研究数据库或缓存。

自动准备 uv 支持 Windows/Linux x64，下载当前使用直连 HTTPS；受限网络可先按官方说明安装 uv。公共 npm 发布需本机完成 npm 登录；未发布前不能宣称 registry 安装成功。新增跨平台 CI 执行 npm 测试、打包及 tgz 的 npx 启动。

状态：本地实现完成，已发布到私有仓库的 [PR #1](https://github.com/magnesium037-dev/quant-research-agent/pull/1)，未合并；部分在线验收待完成。离线测试与在线接口探测的结果分别记录，不将缺失凭据或接口错误标为成功。

## 环境
Windows，Python 3.12；Git 仓库 main 空初始化提交，开发分支 feat/research-agent-mvp。
开始实施时 gh 未登录，模型环境变量未配置；未把聊天中的密钥写入文件或日志。

## 执行结果
依赖导入：vectorbt 0.28.5 导入通过。Windows 最新 NumPy/SciPy wheel 加载失败，Plotly 7 删除的 scattermapbox 配置与 vectorbt 不兼容，已约束 numpy 2.2.6、numba 0.61.2、scipy 1.15.3、scikit-learn 1.6.1、plotly 5.x 并锁定依赖。

2026-09-17 公开接口首次探测（每项独立进程，25 秒上限）：
- stock_info_global_em：成功返回 200 条，含标题、摘要、发布时间、链接。
- tool_trade_date_hist_sina：成功返回 8797 个交易日；具体覆盖终点需每次实验检查。
- fund_etf_hist_em：510300、2024-01-01 至 2024-01-31、hfq，成功返回 22 行 OHLCV。
- stock_news_em：关键词 ETF，成功返回 10 条。
- stock_individual_notice_report：600519，2026-01-01 至 2026-09-17，返回 62 条公告目录。
- stock_research_report_em：600519，返回 771 条研报目录，含 PDF 链接。
- fund_announcement_report_em：510300，返回 100 条定期报告目录；没有直接正文 URL，不合成链接。

这些结果仅证明当次连通及结构，不证明数据准确、覆盖当前日期或接口持续可用。
封装接口在线复测：新闻 10 条、公司公告默认日期 20 条、基金目录 20 条、研报 20 条、ETF 行情 2 条、日历 8797 日。历史接口复测遇 ProxyError，原始 AKShare 同样失败；首次成功不代表持续连通。公网材料 example.com 下载触发 20 秒超时，真实网页正文读取尚未在线验收通过。

2026-09-20 最终本地验收：39 项 unittest 离线测试通过；CLI help 启动及 uv pip check 通过。测试覆盖工具预算/权限/超时/报告引用、数据时效/目录/错误、材料 SSRF/重定向/TLS/文件授权、记忆审批事务/幂等/重启、留出区间重复查看，以及共享现金/次开盘/先卖后买/费用/跳空部分成交/短周/未来信号隔离/留出现金重置。

集成测试使用真实 SQLite、材料解析与 vectorbt 引擎，假模型和合成行情：提问 → 读取材料 → 运行多 ETF 实验 → 带来源报告 → 提议记忆 → CLI 用户批准 → 重启读取 → 修改后下一请求生效。外部文本不能批准记忆或执行 Shell。

独立审查发现并修复 Windows 未授权 UNC 路径在授权检查前 resolve 的问题；新增测试确认未授权路径不进入文件系统解析。另修复 Windows CRLF 段落分隔，保证来源段落编号一致。

2026-09-20 真实 CLI 回测探测：510300/510500、2024-01-01 至 2024-03-29、lookback=20，默认网络出现 AKShare 历史接口 ProxyError；仅为探测使用直连后仍超时。失败运行已在本机忽略目录 smoke-results 留档，未产生可宣称成功的真实组合结果。

模型在线验收：doctor 确认 LLM_BASE_URL、LLM_MODEL、LLM_API_KEY 均未加载，因此未调用真实模型。用户已完成 GitHub CLI 授权，账号 magnesium037-dev。Windows/Ubuntu Python 3.12 CI 已配置，远端结果以 PR checks 为准。
