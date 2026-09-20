# 首版开发与验收

## 产品边界
分发补充：npm 包仅是 Node.js 20+ 启动器，透传参数、标准输入输出和退出码，以 uv run --locked 运行原有 Python CLI。无需预装 Python 或 uv；优先使用本机 uv，否则下载固定版本官方 uv 并验证 SHA256，再准备 Python 3.12 和依赖。保留调用方工作目录，无 shell 参数拼接，不在 npm install 阶段执行脚本。包采用 MIT 开源许可，GitHub 默认分支为可运行分支，安装命令为 `npm install -g --allow-git github:magnesium037-dev/quant-research-agent`；新版 npm 可能默认禁用 Git 依赖，npx 同样传 `--allow-git`。CLI 命令为 qagent；qagent 名称已被 npm 上的其他包占用，不能覆盖。公开 GitHub 安装不要求下载者登录 npm。CI 在 Windows/Ubuntu 验证 Node 20+ 启动器及实际 tgz 的 npx 启动。
本地 CLI 独立 Agent，用户主动请求新闻/公告/研报研究，个人记忆经确认后生效，多 ETF 动量轮动。免费公开源；不实盘、不后台监控、不网页、不任意代码执行。

## 基线与模块契约
Python 3.12, argparse, OpenAI SDK (Chat Completions), SQLite, AKShare, vectorbt, pandas, httpx, beautifulsoup4, pypdf.
Package: src/qagent. Command qagent. Runtime data: QAGENT_HOME or ~/.qagent, never source directory. UTF-8 JSON/Markdown. UTC ISO timestamps with source times preserved; market calendar Asia/Shanghai.

- store.py: Store(home: Path), .memory_add(text, source='user', pending=False)->dict; .memory_list(status='approved')->list; .memory_edit(id,text), .memory_delete(id), .memory_approve(id), .memory_reject(id). Approved/pending/rejected are separate states, transactional/idempotent approval. .create_run(question,kind='research')->str; .event(run_id,kind,payload); .finish_run(run_id,result,status='completed'); .list_runs()->list; .get_run(id)->dict; .save_snapshot(payload)->str SHA256; .record_experiment(run_id,params,snapshot_id,result)->dict marking repeated holdout. .close(). Database values JSON serializable. No model approval tool.
- data.py: get_news(keyword='',days=7,limit=20)->dict; get_quotes(symbols:list[str])->dict; get_disclosures(symbol,kind='company',start='',end='',limit=20)->dict; get_research_reports(symbol,limit=20)->dict; get_history(symbol,start,end,adjust='hfq')->pandas.DataFrame with datetime index and Open High Low Close Volume; get_calendar()->pandas.DatetimeIndex. Lazy imports. Strict errors; bounded AKShare calls. Include source metadata in outputs.
- materials.py: read_material(location:str, allowed_files:list[str]|None=None)->dict. Local files only explicitly supplied by CLI user, not any path guessed by model. Public HTTP(S), block nonpublic DNS/IP and redirects; pinned validated addresses to prevent DNS rebinding; size/time/type bounds. HTML paragraphs and text PDF pages, no OCR. Body only data.
- backtest.py: run_backtest(symbols,start,end,lookback=60,top_k=2,rebalance='weekly',initial_cash=100000,store=None,run_id=None)->dict. Dependencies injectable/internal simulation function for offline synthetic tests. Shared-cash vectorbt. Caller-independent validation.
- agent.py: run_research(question,store,client=None,model=None,allowed_files=None)->dict containing run_id/report/status. Model from LLM_MODEL; base URL LLM_BASE_URL; key LLM_API_KEY; no fallback provider. Max12 model calls,24 tool calls,12 experiments including repeats. Tool schema validates all arguments; errors consume budgets. No unbounded tools/shell. max source text/context lengths, timeout/cancellation persist partial progress. Genuine sources get IDs [S1] etc; fabricated citations rejected; deterministic backtest metrics appended by renderer. Model sees approved memories, bounded current task, queries past runs on demand.
- cli.py: main(argv=None)->int. 裸运行默认进入 chat；显式支持 chat, ask QUESTION [--file PATH repeatable], backtest --symbols CODE ... --start YYYY-MM-DD --end YYYY-MM-DD [--lookback 20|60|120 --top-k 1|2 --rebalance daily|weekly], memory add/edit/delete/list/pending/approve/reject, runs list/show, doctor [--online]. Imports of later modules lazy. Offline doctor shows configuration presence only; online doctor explicitly verifies function calling.

Data tool envelope: {items:list, source:str, fetched_at:str, warnings:list}. Each item: title, source_type, url (nullable), published_at(nullable), content, content_status(summary/directory/full), optional page/paragraph. Raw quote fields retained where necessary. News age filter enforced, unknown dates labeled. Directory != full text. Errors returned to model honestly. Never silently use stale cache as live.

## 投资方法与实验
固定2–20只ETF；参数lookback=20/60/120，top_k=1/2，频率每日/每周。默认60/2/weekly。正动量降序，代码破同分，每个入选权重0.99/k，不足留现金。
所有信号前收盘，次交易日开盘；上一收盘定量，交易前同一权益分母，先卖后买，单一现金池，无负现金无做空，跳空可部分成交。vectorbt.from_orders: targetpercent, cash_sharing/group_by, val_price=-inf, update_value=False, call_seq=auto, direction=longonly. 非调仓NaN，清仓0。首根估值预热不计评价。
日线后复权研究单位；展示价与模拟价分开。默认排除今天，日历必须覆盖区间和周信号判断。请求窗口及热身任一ETF缺日/零量/非法OHLC/历史不足，明确失败；不取静默交集，不补成交。
完整评价段及前70%开发、后30%留出分别现金开始运行，同期等权买入持有/现金基准。5bp单边综合摩擦，0/10bp敏感性。输出净值、收益、最大回撤、换手、费用、持仓现金、目标与实际权重、订单/部分成交。252日年化。快照哈希、全部尝试、留出被查看状态均留档。
各段策略保持原日/周调仓日程，首日无信号则持币等待；等权基准首日目标每只1/N，仍以前收盘定量并允许费用/跳空部分成交。一个参数实验包含三段、三档费用下的策略和基准确定性计算，不将这些比较视为额外调参。

实验结果公共字段：`status`、`parameters`、`snapshot_id`、`holdout_start`、`holdout_end`、`summary`、`details`、`limitations`。`summary` 含 full/development/holdout 各段策略、等权和现金指标及成本敏感性；`details` 保存逐日净值/持仓/成交。模型和终端只接收摘要，完整结果留档。失败也调用 record_experiment，使用 status=failed、error，无法形成快照时 snapshot_id 可为空。Store 对成功实验的留出日期区间做重叠检查（不受参数或快照哈希改变影响），返回 holdout_previously_viewed；标记只表示曾使用，不能证明其他渠道没有查看过。
限制随每份结果：后复权小数单位、同开盘价格顺序撮合；未建模整手/最低佣金/分红到账/涨跌停队列/冲击；固定池选择偏差。当前新闻不进入历史信号。

## 信息与记忆
AKShare快讯、关键词新闻、ETF行情、个股公告/研报目录、基金定期报告目录。正文读取公开URL及用户授权TXT/MD/文本PDF。交易所/发行人公告为原始披露；研报作者观点；社区待核实。保留来源/发布时间/抓取时间/正文状态/页段定位。失败、空结果、扫描PDF、登录限制明确报告。报告结构：问题、证据、影响推理、反证、未知项、下一步验证。
SQLite approved/pending记忆、会话事件、实验记录。用户可编辑删除；Agent仅提议。批准确保事务与幂等；记忆只在下一请求重新加载，历史实验不自动变成长久投资规律。

## 顺序与验收
0根代理文档契约；1子A运行循环/CLI；2子B数据/材料；3子C记忆/留档；4子B组合回测；5根代理集成+独立审查。各批仅改所属文件，测试用fake/合成数据。先读文档查询CodeGraph。
unittest:工具调用配对/参数/超时/预算；新闻时效/目录引用/SSRF与材料注入；记忆提议批准拒绝重启幂等；下一日成交/未来数据不泄漏/两ETF共享现金/费用/跳空/短周/缺日/负动量/留出新账户/快照重跑；端到端合成研究。CI Windows+Ubuntu/Python3.12，默认离线。真实接口与配置模型在线探测单独记录。
一次PR：main空基线，feat/research-agent-mvp包含全部变更。私有quant-research-agent，根代理提交开PR，不合并。秘钥、个人资料和运行缓存不入库。远端gh登录与本地模型配置缺失只阻止对应在线验证/发布，不能伪报完成。
