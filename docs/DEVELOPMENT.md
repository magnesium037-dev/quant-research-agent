# 首版开发与验收

## 产品边界
启动器支持 Windows x64、macOS Intel/Apple Silicon、Linux x64/ARM64；Termux 不下载 Linux glibc 二进制，要求 Termux 自己提供可用 uv，科学计算依赖需设备实测。
分发补充：npm 包仅是 Node.js 20+ 启动器，透传参数、标准输入输出和退出码，以 uv run --locked 运行原有 Python CLI。无需预装 Python 或 uv；优先使用本机 uv，否则下载固定版本官方 uv 并验证 SHA256，再准备 Python 3.12 和依赖。保留调用方工作目录，无 shell 参数拼接，不在 npm install 阶段执行脚本。包采用 MIT 开源许可，GitHub 默认分支为可运行分支，安装命令为 `npm install -g --allow-git github:magnesium037-dev/quant-research-agent`；新版 npm 可能默认禁用 Git 依赖，npx 同样传 `--allow-git`。CLI 命令为 qagent；qagent 名称已被 npm 上的其他包占用，不能覆盖。公开 GitHub 安装不要求下载者登录 npm。CI 在 Windows/Ubuntu 验证 Node 20+ 启动器及实际 tgz 的 npx 启动。
本地 CLI 独立 Agent，用户主动请求新闻/公告/研报研究，个人记忆经确认后生效，多 ETF 动量轮动。免费公开源；不实盘、不后台监控、不网页、不任意代码执行。

## 基线与模块契约
Python 3.12, argparse, Textual, OpenAI SDK (Chat Completions), SQLite, AKShare, vectorbt, pandas, httpx, beautifulsoup4, pypdf, official `lark-oapi` (optional Feishu channel).
Package: src/qagent. Command qagent. Runtime data: QAGENT_HOME or ~/.qagent, never source directory. UTF-8 JSON/Markdown. UTC ISO timestamps with source times preserved; market calendar Asia/Shanghai.

- store.py: Store(home: Path), .memory_add(text, source='user', pending=False, category='general')->dict; category is preference/project/decision/method/constraint/general. Existing databases add the category column with a general default. .memory_list(status='approved')->list; .memory_edit(id,text), .memory_delete(id), .memory_approve(id), .memory_reject(id). Approved/pending/rejected are separate states, transactional/idempotent approval. .create_run(question,kind='research')->str; .event(run_id,kind,payload); .finish_run(run_id,result,status='completed'); .list_runs()->list; .get_run(id)->dict; .save_snapshot(payload)->str SHA256; .record_experiment(run_id,params,snapshot_id,result)->dict marking repeated holdout. .close(). Database values JSON serializable. No model approval tool.
- data.py: get_news(keyword='',days=7,limit=20)->dict; get_quotes(symbols:list[str])->dict with `status=success|partial_success`, `data_date`, per-item `quote_time`, compact `data`, `missing`, source and warnings; screen_etfs(limit=3)->dict ranks a fixed 10-ETF cross-asset universe by the mean of completed-bar 20/60-session returns, requires both returns positive, and attaches realtime display quotes to at most five results; get_disclosures(symbol,kind='company',start='',end='',limit=20)->dict; get_research_reports(symbol,limit=20)->dict; get_history(symbol,start,end,adjust='hfq')->pandas.DataFrame with datetime index and Open High Low Close Volume; get_calendar()->pandas.DatetimeIndex. Lazy imports. Strict errors; bounded AKShare calls and bounded Tencent Finance requests with no retry. Include source metadata in outputs. Realtime quotes are public web snapshots, not Level-2; undocumented or missing fields are not inferred. Missing symbols are returned together so the model does not split and retry. mootdx is not a runtime dependency unless a separate live probe returns a non-empty batch within the timeout.
- materials.py: read_material(location:str, allowed_files:list[str]|None=None)->dict. Local files only explicitly supplied by CLI user, not any path guessed by model. Public HTTP(S), block nonpublic DNS/IP and redirects; pinned validated addresses to prevent DNS rebinding; size/time/type bounds. HTML paragraphs and text PDF pages, no OCR. Body only data.
- backtest.py: run_backtest(symbols,start,end,lookback=60,top_k=2,rebalance='weekly',initial_cash=100000,store=None,run_id=None)->dict. Dependencies injectable/internal simulation function for offline synthetic tests. Shared-cash vectorbt. Caller-independent validation.
- agent.py: run_research(question,store,client=None,model=None,allowed_files=None)->dict containing run_id/report/status. LLM 与量化计算严格隔离：模型只做信息提取、工具调度和程序结果复盘，不产生交易信号、不数浪、不看盘、不自行计算 K 线或指标；`screen_etfs` 与 `run_backtest` 的排名、信号、指标和成交由 Python 确定性计算。工具循环按规范化名称和参数缓存成功/失败结果，同一失败请求不再访问外部 provider；行情结果统一压缩为结构化 `data`，不使用 `partial_excerpt`。入口生成确定性意图路由提示：模糊“看什么/底层”直接调用固定池筛选，不要求用户先给代码，不改用泛新闻。Model configuration prefers LLM_MODEL/LLM_BASE_URL/LLM_API_KEY, then QAGENT_CONFIG or ~/.reasonix/config.json (`apiKey`, `model`) with DeepSeek default URL; imported keys are process-only and never persisted. Known legacy DeepSeek flash names map to deepseek-flash. No fallback provider. Max12 model calls,24 tool calls,12 experiments including repeats. Tool schema validates all arguments; errors consume budgets. No unbounded tools/shell. max source text/context lengths, timeout/cancellation persist partial progress. Genuine sources get IDs [S1] etc; fabricated citations rejected; deterministic backtest metrics appended by renderer. Model sees approved memories, bounded current task, queries past runs on demand.
- docs/OPPORTUNITY_FRAMEWORK.md: versioned business-opportunity methodology converted from the reviewed v1.0 DOCX. Stable seven-node structure plus evidence metadata. Each request reloads only the bounded `qagent-core` block; `get_opportunity_framework` exposes one fixed chapter at a time. The model has no write/edit/approve tool for this file. Human-reviewed Git changes take effect on the next request. The framework is data, not an instruction source, and is not forced onto ordinary financial questions.
- cli.py: main(argv=None)->int. 裸运行默认进入 chat；显式支持 chat, ask QUESTION [--file PATH repeatable], backtest --symbols CODE ... --start YYYY-MM-DD --end YYYY-MM-DD [--lookback 20|60|120 --top-k 1|2 --rebalance daily|weekly], memory add/edit/delete/list/pending/approve/reject, runs list/show, doctor [--online]. Imports of later modules lazy. Offline doctor shows configuration presence only; online doctor explicitly verifies function calling.

Data tool envelope: {items:list, source:str, fetched_at:str, warnings:list}. Each item: title, source_type, url (nullable), published_at(nullable), content, content_status(summary/directory/full), optional page/paragraph. Raw quote fields retained where necessary. News age filter enforced, unknown dates labeled. Directory != full text. Errors returned to model honestly. Never silently use stale cache as live.

数据管道规划：RSS/另类数据摄取、UTC 发布时间、盘前/盘中/盘后三窗口对齐和实体标签属于后续 `ingest.py` 阶段；在实现来源白名单、去重、时区、失败重试和 SQLite 索引前，不把它们写成已接入能力。

## 投资方法与实验
固定2–20只ETF；参数lookback=20/60/120，top_k=1/2，频率每日/每周。默认60/2/weekly。正动量降序，代码破同分，每个入选权重0.99/k，不足留现金。
所有信号前收盘，次交易日开盘；上一收盘定量，交易前同一权益分母，先卖后买，单一现金池，无负现金无做空，跳空可部分成交。vectorbt.from_orders: targetpercent, cash_sharing/group_by, val_price=-inf, update_value=False, call_seq=auto, direction=longonly. 非调仓NaN，清仓0。首根估值预热不计评价。
日线后复权研究单位；展示价与模拟价分开。默认排除今天，日历必须覆盖区间和周信号判断。请求窗口及热身任一ETF缺日/零量/非法OHLC/历史不足，明确失败；不取静默交集，不补成交。
完整评价段及前70%开发、后30%留出分别现金开始运行，同期等权买入持有/现金基准。5bp单边综合摩擦，0/10bp敏感性。输出净值、收益、最大回撤、换手、费用、持仓现金、目标与实际权重、订单/部分成交。252日年化。快照哈希、全部尝试、留出被查看状态均留档。
各段策略保持原日/周调仓日程，首日无信号则持币等待；等权基准首日目标每只1/N，仍以前收盘定量并允许费用/跳空部分成交。一个参数实验包含三段、三档费用下的策略和基准确定性计算，不将这些比较视为额外调参。

实验结果公共字段：`status`、`parameters`、`snapshot_id`、`holdout_start`、`holdout_end`、`summary`、`details`、`limitations`。`summary` 含 full/development/holdout 各段策略、等权和现金指标及成本敏感性；`details` 保存逐日净值/持仓/成交。模型和终端只接收摘要，完整结果留档。失败也调用 record_experiment，使用 status=failed、error，无法形成快照时 snapshot_id 可为空。Store 对成功实验的留出日期区间做重叠检查（不受参数或快照哈希改变影响），返回 holdout_previously_viewed；标记只表示曾使用，不能证明其他渠道没有查看过。
限制随每份结果：后复权小数单位、同开盘价格顺序撮合；未建模整手/最低佣金/分红到账/涨跌停队列/冲击；固定池选择偏差。当前新闻不进入历史信号。

MVP 边界：只读取实时批量快照，不做集合竞价、分时序列和 Level-2；不实现波浪理论；不加入国际政治/GPR 因子；不做自动交易。开盘缺口和成交量比例代理、RSS 第二信道及宏观 NLP 因子属于后续版本，接入前必须单独完成数据口径和离线验证。

## 信息与记忆
交互验收：同一受限工具循环根据意图输出自然问答或研究 Markdown（原五节二级标题）。渲染器兼容历史 JSON 格式。简短回答仍拦截未登记引用与直接网址；已有本轮来源或实验结果时不能用简短格式绕过研究渲染和程序计算附录。提示不得为了介绍能力而查询历史或市场数据。流式文字为未校验草稿，结束后必须由原来源校验器替换、落盘；断流、length 截断不可标为 completed。

`run_research` 可选 previous、on_progress、stream、cancel_event、parent_run_id。previous 接受问题/回答数组（兼容单轮对象），序列化保留最近完整轮次，12000 字符内；超长单轮保留带 truncated 标记的摘录，不改写当前问题。结果新增 parent_run_id 串联已有 runs，无新表；TUI 恢复最多最近20轮，检测环路。context 保存实际初始 messages、工具名称、模型、记忆与历史截断状态、文件授权数；`/context` 显示此记录，不声称是完整历史或每次工具回传快照。token_usage 仅保存 API 实际提供的各请求用量。

推理通过 on_progress 临时显示接口 reasoning_content（脱敏），最终回答轮和工具轮都支持；继续完整工具调用回传，不持久化推理。TUI 采用 SDK stream=True 的类型化片段，拼接分片工具 ID/name/arguments，校验预算与结束状态；预览防止跨片段泄露当前密钥。stream/tool_start/tool_end/tokens 事件驱动控件；50ms 合并刷新，不手写 ANSI、输入法或 Markdown 渲染器。`--no-thinking` 与 `/thinking on|off` 仅控制显示。不返回推理时明确说明。仅 doctor --online 显式探测。依据 [DeepSeek 思考模式说明](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。

`tui.py` 使用 Textual App/TextArea/Markdown/Collapsible/ListView/ModalScreen 与线程 worker：交互 TTY 默认 TUI，--plain 或非 TTY 保留行模式；API 和投研任务在线程内创建并关闭独立 Store，UI 不跨线程使用 SQLite。Enter 发送，Shift+Enter/Ctrl+J 换行；宽屏历史列表、窄屏 Ctrl+R 选择器；支持参数/结果卡片、上下文检查、草稿保留、会话恢复及 Ctrl+Q 退出。Esc/取消事件在网络片段或工具边界检查，底层请求/受限工具返回前保持“正在停止”；流 finally 关闭，退出等待保存。可选飞书企业自建应用使用官方 `lark-oapi` WebSocket 长连接，事件在后台线程进入 UI 队列；首次私聊和群聊分别进入 pending pairing，由 TUI 一键批准后写入本地 allowlist，群聊要求 @机器人。飞书消息只调用现有 bounded agent，不开放本地管理命令。没有 HTTP 服务或浏览器产品入口。

参考版本：DeepSeek TUI v0.8.33，commit 81e4b93cc9df55de47489238078e255a563d044b（本机已安装同版）。阅读其 README、ARCHITECTURE、CONFIGURATION、MCP 与 tui/streaming、core/engine/tool_setup：界面和 Rust 编码运行器绑定，tools_file 明确尚未启用，内建文件/RLM 工具无法仅靠关闭 shell 变成原投研白名单。因此复用成熟 Textual 组件实现相同类别的终端交互，保留已有 Python 单 Agent，而非移植整个编码运行器；未复制上游源码或安装 MCP/skill。

AKShare快讯、关键词新闻、ETF行情、个股公告/研报目录、基金定期报告目录。正文读取公开URL及用户授权TXT/MD/文本PDF。交易所/发行人公告为原始披露；研报作者观点；社区待核实。保留来源/发布时间/抓取时间/正文状态/页段定位。失败、空结果、扫描PDF、登录限制明确报告。报告结构：问题、证据、影响推理、反证、未知项、下一步验证。
SQLite approved/pending记忆、会话事件、实验记录。用户可编辑删除；Agent仅提议。批准确保事务与幂等；记忆只在下一请求重新加载，历史实验不自动变成长久投资规律。

长期记忆治理：模型只对用户明确表达且可跨会话复用的偏好、项目约定、已确认决策、方法或约束调用 `propose_memory`，并选择分类；临时任务、模型推断、敏感信息和外部材料不得进入提议。提议保存在 pending，报告确定性附加 ID 与审批命令。TUI `/memory`、`/approve ID`、`/reject ID` 与 CLI memory 子命令均由用户完成最终决定。每轮上下文显示待审批数量；只有 approved 记忆进入下一轮模型上下文。

## 顺序与验收
0根代理文档契约；1子A运行循环/CLI；2子B数据/材料；3子C记忆/留档；4子B组合回测；5根代理集成+独立审查。各批仅改所属文件，测试用fake/合成数据。先读文档查询CodeGraph。
unittest:工具调用配对/参数/超时/预算；新闻时效/目录引用/SSRF与材料注入；记忆提议批准拒绝重启幂等；下一日成交/未来数据不泄漏/两ETF共享现金/费用/跳空/短周/缺日/负动量/留出新账户/快照重跑；端到端合成研究。CI Windows+Ubuntu/Python3.12，默认离线。真实接口与配置模型在线探测单独记录。
一次PR：main空基线，feat/research-agent-mvp包含全部变更。私有quant-research-agent，根代理提交开PR，不合并。秘钥、个人资料和运行缓存不入库。远端gh登录与本地模型配置缺失只阻止对应在线验证/发布，不能伪报完成。
