# 验收记录

## 行情批量与工具重试治理（2026-09-21）

`get_quotes` 现在一次返回 `success/partial_success`、`data_date`、紧凑字段表和 `missing` 清单；provider 的折价率字段保留原值并标记待核查。Agent 按工具名和参数缓存结果，同一失败请求不再重复访问外部接口，工具上下文不再生成 `partial_excerpt`。新增数据日期、缺失批量和重复失败短路测试；未接入未经核验的 ETF 静态黑名单或 RSS 管道。

## LLM 与量化隔离（2026-09-21）

系统提示、`run_backtest` 工具契约和 Python 回测模块明确：LLM 只做信息提取、工具调度和程序结果复盘；K 线、指标、信号和成交由 Python 确定性计算。新增离线测试确认隔离规则进入实际模型上下文。RSS 多源摄取、发布时间窗口对齐和实体标签尚未实现，不宣称已接入。MVP 同时排除集合竞价、分时、波浪理论、GPR 和自动交易。

## 记忆治理与审批入口（2026-09-21）

长期记忆提议增加 preference/project/decision/method/constraint/general 分类；旧数据库启动时原位增加 category 列并默认 general。模型仍只能创建 pending 建议，最终报告确定性显示内容、分类、ID 与批准命令。TUI 支持 `/memory`、`/approve ID`、`/reject ID`，上下文栏显示待审数量。55 项 Python 离线测试及 4 项 Node 启动器测试通过，覆盖旧库迁移、分类校验、报告提示和 TUI 审批。未加入自动批准、向量数据库、第二套情景日志或后台巩固任务。

## 商业机会方法库（2026-09-21）

将 18 页《项目与商业机会判断架构 v1.0》转换为可版本控制的 `docs/OPPORTUNITY_FRAMEWORK.md`，保留 333 个正文段落、17 个表格中的结构信息和 38 个参考链接；七个核心节点各有独立章节。标准渲染器因本机缺少随附 LibreOffice 无法启动，改用本机 Microsoft Word 导出 PDF，并逐页检查全部 18 页，未发现截断或排版破损。

每次模型请求重新读取受边界标记约束的核心索引；商业机会问题可按需调用只读章节工具。界面显示实际注入的方法字符数。模型没有修改、批准或自动吸收方法库的工具，文档更新仍需人工评审并由 Git 留痕。

50 项 Python 离线测试及 4 项 Node 启动器测试通过；打包预检确认 Markdown 方法库包含在 npm 包内。未发起真实模型、行情或外部资料请求。

## Textual 终端交互（2026-09-20）

参考本机 DeepSeek TUI 0.8.33 对应源码，采用 Textual 8.2.8 的现成终端控件与 worker。49 项 unittest（包含 Textual run_test）及 4 项 Node 启动器测试通过。覆盖分片工具参数、推理完整回传、跨片段密钥脱敏、流关闭、截断不发布、取消落盘、草稿保留、工具卡片、历史父链、上下文弹窗与窄屏历史选择器。以合成内容导出宽/窄屏终端截图检查排版；浏览器仅用于查看导出的 SVG，预览服务器已关闭，产品没有 Web 入口。

本机 npm 全局入口已通过本地项目链接更新；实际 `qagent chat --help` 包含 --plain，新版在 Windows 真实 PTY 中显示 Textual 界面，Ctrl+Q 正常退出。未发起真实模型或行情请求，流式行为由合成 SDK 片段验证；未发布远端。此记录取代上一节“全局未更新/非流式”的交互状态，既有线上数据源限制仍然适用。

## 对话、上下文与推理显示修复（2026-09-20）

45 项 unittest 离线测试通过，包含新增的简短问答、来源校验不可绕过、实际初始上下文记录、记忆/前轮摘录截断、推理显示及密钥脱敏、不落盘推理，以及交互会话续问、/context、/thinking off、/new。CLI chat --help 和 diff 格式检查通过。使用假模型验证接口字段处理，本次未进行真实 DeepSeek 在线请求；未替换全局 npm 安装或发布远端版本。

## npm 分发验收（2026-09-20）

用户已授权公开 npm 包；GitHub 仓库保持私有。npm 包包含完整应用源码及 Python 锁文件，运行环境在首次启动时准备，不是离线自包含二进制。

Windows Node.js 22：4 项 Node 离线测试通过；真实官方 uv 0.12.17 下载、SHA256 校验、解压及版本检查通过。实际 tgz 通过 npx 启动；独立全局安装前缀中的 qagent 安装 89 个锁定依赖后成功启动，中文记忆新增和重启读取通过。打包白名单共 16 个文件，不包含凭据、研究数据库或缓存。

自动准备 uv 支持 Windows/Linux x64，下载当前使用直连 HTTPS；受限网络可先按官方说明安装 uv。项目采用 MIT 开源许可，GitHub 默认分支为可运行分支；npm 名称 qagent 已被其他包占用，仓库内 npm 元数据名为 qagent-research，CLI 命令为 qagent。公开 GitHub 安装不要求下载者登录 npm；新版 npm 禁用 Git 依赖时需使用 `--allow-git`，实际安装命令为 `npm install -g --allow-git github:magnesium037-dev/quant-research-agent`。新增跨平台 CI 执行 npm 测试、打包及 tgz 的 npx 启动。

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

模型在线验收补测（2026-09-20）：此前仅检查环境变量，遗漏了用户已经提供的测试凭据；本次将凭据仅注入临时进程环境，未持久化密钥。DeepSeek 官方 API 返回 deepseek-flash/deepseek-v4-pro；deepseek-flash 的真实工具调用探测通过。使用实际 npm 全局安装包运行 ask，真实模型调用 read_material 读取明确标记的合成材料，生成六部分报告及 3 条真实段落引用，运行状态 completed。未请求新闻、回测或写入记忆；这项验证不代表真实行情回测已通过。

配置适配补测：读取用户已有的 Reasonix 配置成功，旧模型名映射为 `deepseek-flash`，doctor 在线工具探测通过；密钥未显示或写入 qagent 数据。

用户已完成 GitHub CLI 授权，账号 magnesium037-dev。Windows/Ubuntu Python 3.12 的 39 项测试和新增 npm 的 4 项测试、实际打包启动均通过，详见 PR checks。npm 发布仍待 npm 账号登录。
