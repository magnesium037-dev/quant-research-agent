# qagent

本地运行的证据驱动研究助手。它能查询公开资料、读取用户明确授权的材料、运行固定的多 ETF 动量实验，并在用户批准后跨会话使用长期记忆。它也支持创业、产品、副业、项目和职业机会分析。

它是一个本地 CLI 单 Agent：Node.js 只负责启动和准备 uv，实际逻辑运行在 Python；交互界面是 Textual 终端，不是网页。模型只能调用项目定义的工具，不能执行任意 Shell、下单或自行批准记忆。

详细接口和验收约束见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，商业机会方法库见 [docs/OPPORTUNITY_FRAMEWORK.md](docs/OPPORTUNITY_FRAMEWORK.md)，实际测试记录见 [docs/VALIDATION.md](docs/VALIDATION.md)。

## 架构

```text
qagent / qagent chat
        ↓
Node 启动器 → uv + Python 3.12
        ↓
CLI → Textual TUI 或 plain 行模式
        ↓
agent.run_research
        ↓
模型循环 + 显式工具白名单
   ├─ data.py       行情、新闻、公告和研报目录
   ├─ materials.py  授权文件和受限公开材料
   ├─ backtest.py   固定 ETF 动量轮动实验
   ├─ 方法库         商业机会七节点 Markdown
   └─ store.py      SQLite 记忆、运行、事件和实验记录
```

运行数据保存在 `QAGENT_HOME`，默认是 `~/.qagent`；源代码目录不保存个人记忆或研究缓存。模型密钥只存在于当前进程，不写入报告或 SQLite。

## 安装和配置

### GitHub / npm

需要 Node.js 20+。首次运行会准备固定版本的 uv、Python 3.12 和锁定依赖。

启动器支持 Windows x64、macOS Intel/Apple Silicon、Linux x64/ARM64。Termux 使用 Android 自己的运行环境，需先在 Termux 中安装可用的 `uv`；启动器不会把 Linux glibc 二进制误装到 Android。Termux 上的科学计算依赖仍需以实际设备安装结果为准。

```powershell
npm install -g --allow-git github:magnesium037-dev/quant-research-agent
qagent doctor
qagent chat

# 不安装到全局
npx --yes --allow-git --package github:magnesium037-dev/quant-research-agent qagent --help
```

首次准备需要访问 GitHub Releases 和 Python 包下载服务；环境准备完成后会复用本地缓存。Node.js 18 及以下不受支持。

### Python 开发环境

```powershell
uv sync --locked
$env:LLM_BASE_URL = 'https://api.deepseek.com'
$env:LLM_MODEL = 'deepseek-flash'
# 请在本机安全设置 LLM_API_KEY
uv run qagent doctor
uv run qagent doctor --online  # 明确发起一次真实 API 调用
```

也可以从 `QAGENT_CONFIG` 或 `~/.reasonix/config.json` 读取 `apiKey`、`model` 和 `baseUrl`。显式环境变量优先。项目使用 OpenAI Chat Completions 兼容接口，不会静默切换模型或供应商。

### 飞书双向聊天（可选）

配置飞书企业自建应用的机器人能力和 `im.message.receive_v1` 事件后，在启动 TUI 前设置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`，再运行 `qagent chat`。TUI 使用官方 `lark-oapi` SDK 建立 WebSocket 长连接；首次私聊或群聊 `@机器人` 会弹出本机绑定确认，未绑定会话不会调用模型。群聊绑定与私聊分开，群聊始终要求 `@机器人`。飞书侧不开放本机记忆审批等管理命令。

## 最小用法

```powershell
qagent                         # 进入 Textual 终端界面
qagent chat --plain            # 简易行模式
qagent ask '最近一周有哪些影响宽基 ETF 的事件？请列证据与反证。'
qagent ask '区分这份材料中的事实与作者观点' --file 'C:/Research/report.pdf'

qagent backtest --symbols 510300 510500 159915 `
  --start 2022-01-01 --end 2024-12-31 --lookback 60 --top-k 2 --rebalance weekly
```

`ask` 和 `chat` 支持 `--no-thinking`。TUI 中 Enter 发送，Shift+Enter/Ctrl+J 换行，Ctrl+T 切换推理显示，Ctrl+X 查看实际初始上下文，Ctrl+R 选择历史，Esc 取消当前运行，Ctrl+Q 退出。接口没有返回推理时，界面会明确说明，不会编造 Chain of Thought。

## 记忆治理

记忆分为两层：`runs/events` 保存情景记录，`memories` 保存长期候选和已批准内容。模型可以把用户明确表达的长期偏好、项目约定、已确认决策、方法或约束提交为 `pending` 建议，但不能直接写成生效记忆。

```text
用户表达长期信息
        ↓
模型 propose_memory（带分类）
        ↓
pending：preference / project / decision / method / constraint / general
        ↓ 用户批准
approved
        ↓
下一次请求注入上下文
```

报告会显示待确认记忆的完整 ID 和审批命令。TUI 支持：

```text
/memory
/approve MEMORY_ID
/reject MEMORY_ID
```

CLI 支持：

```powershell
qagent memory add '项目使用 Python 和 SQLite' --category project
qagent memory pending
qagent memory approve MEMORY_ID
qagent memory reject MEMORY_ID
qagent memory list
```

旧版 SQLite 数据库启动时会自动补上 `category` 字段，并将旧记忆归为 `general`。当前版本不会后台自动批准、静默巩固或删除记忆；这保证长期上下文仍由用户控制。

## 商业机会分析

商业问题会读取 [项目与商业机会方法库](docs/OPPORTUNITY_FRAMEWORK.md) 的核心索引，必要时再读取具体章节。七个节点是：需求、使用者、场景、替代方案、从知道到行动、市场与竞争、我为什么做它。

方法库要求每个判断区分：

```text
观测 → 解释 → 竞争解释 → 证据强度 → 下一步验证
```

方法库本身是版本控制的研究资料，不是自动学习日志。模型可以提出修订建议，修改必须经过人工审阅并由 Git 留痕；普通金融问题不会强行套用这套框架。

## 回测口径

回测只接受固定参数网格：lookback 为 20/60/120 日，top_k 为 1/2，再平衡为 daily/weekly。默认 60 日、top_k=2、weekly。信号使用前收盘，下一交易日开盘成交；共享现金、先卖后买、只做多，不自动补齐缺失行情。

结果分别计算完整区间、前 70% 开发段和后 30% 留出段，并比较 0/5/10bp 成本。失败、缺失数据、目录与正文的区别都会明确写出。示例 ETF 仅用于演示，不构成投资建议或真实账户收益承诺。

LLM 与量化严格隔离：LLM 只做信息提取、工具调度和结果复盘；不产生交易信号、不数浪、不看盘，也不自行计算 K 线或指标。所有行情、指标、信号、竞价和成交都由 Python 确定性代码完成。

行情工具通过腾讯财经公开网页接口一次批量取得实时快照，返回 `status`、`data_date`、`quote_time`、紧凑 `data` 和 `missing`；它不是交易所 Level-2，接口也没有公开稳定性承诺。缺失代码不会触发拆分重试，未提供字段不会推断或补造。mootdx 仅保留为诊断候选，当前实测返回空表，因此没有加入运行依赖。RSS 多源摄取、发布时间对齐、实体标签和第二信道因子仍是待实现的数据管道，尚未在系统中伪装成可用能力。

用户问“看什么、选哪只、什么底层可看”时，不再要求先提供代码。`screen_etfs` 会在固定 10 只跨资产 ETF 池中，用最近完整交易日的 20/60 日收益等权打分；两段收益都为正才进入候选，默认返回前 3 名，再附上实时展示价。候选池、公式、信号日期和缺失项全部随结果返回，LLM 只能解释，不能改写排名。

## 边界

- 没有实盘下单、后台监控、网页服务或任意代码执行。
- 飞书长连接仅在 TUI 进程运行期间工作；关闭 TUI 不会保持双向机器人在线。密钥不写入 SQLite、报告或日志。
- MVP 只读取批量实时快照，不做集合竞价、分时序列、波浪理论和国际政治交易学；开盘缺口与成交量比例代理、GPR 和其他复杂因子分别推迟到后续版本。
- 当前新闻不会进入历史回测信号。
- 新闻、公告和研报目录不会冒充已阅读全文。
- 回测由程序确定性计算，模型只解释返回的数值。
- 默认离线测试使用合成数据和假模型；真实 API、行情连通性和覆盖范围单独验收。

## 开发验证

```powershell
uv run python -m unittest discover -s tests -v
$env:ComSpec = 'C:\Windows\System32\cmd.exe'
npm test
```

当前验收记录见 [docs/VALIDATION.md](docs/VALIDATION.md)。
