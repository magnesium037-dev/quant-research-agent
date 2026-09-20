# Quant Research Agent

本地命令行投研助手：公开资料研究、经用户批准的长期记忆、多 ETF 动量轮动实验。

开发与验收规范见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，协作规则见 [AGENTS.md](AGENTS.md)。

Python 3.12；使用现有 OpenAI Chat Completions 兼容服务。行情和资讯采用免费公开接口，不能保证实时性和完整性。研究回测不是真实账户收益承诺。

架构参考 Hello-Agents / Hermes；投资方法参考 TradingAgents / Qlib / RD-Agent，不引入这些项目的运行框架。

## 安装与模型配置

### npm / npx 包

需要 Node.js 20+。npm 包包含完整应用代码；首次启动自动准备 uv、Python 3.12 和锁定依赖，可能需要数分钟和较多磁盘空间，之后复用安装环境。需要能访问 npm、GitHub Releases 和 Python 包下载服务。

自动准备 uv 支持 Windows x64、Linux x64；其他平台请先按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装 uv，Python 科学计算依赖仍需该平台支持。自动下载使用固定版本及 SHA256 校验，失败会停止并给出提示。

发布到 npm 后，安装包名为 `qagent-research`，CLI 命令仍然是 `qagent`：

```powershell
npm install -g --allow-git github:magnesium037-dev/quant-research-agent
qagent doctor
qagent chat
# 临时运行，不全局安装
npx --yes --allow-git --package github:magnesium037-dev/quant-research-agent qagent --help
```

要求 Node.js 20+。包包含 Python 源码及 uv.lock，不包含密钥、个人记忆和运行缓存。模型环境变量与下方相同，相对材料路径以调用命令时的目录为准。运行环境缓存在 `~/.cache/quant-research-agent`，无需向全局安装目录写入；研究数据独立保存在 `~/.qagent`。npm 安装阶段不运行下载脚本，首次运行 qagent 时准备运行环境。

当前公开仓库默认分支就是可运行分支，因此不需要 `git+` 前缀或分支后缀。新版 npm 可能默认禁用 Git 依赖，需要显式加 `--allow-git`。公开 npm 的包名不能使用 `qagent`，因为该名称已被其他项目占用；本项目保留 npm 元数据名 `qagent-research`，从 GitHub 安装后命令仍为 `qagent`。Node 18 及以下不支持当前启动器。

### Python 开发安装

```powershell
uv sync --locked
$env:LLM_BASE_URL = 'https://api.deepseek.com'
$env:LLM_MODEL = 'deepseek-flash'
# 在本机安全设置 LLM_API_KEY，不把密钥提交到 Git 或发送到聊天中。
uv run qagent doctor
uv run qagent doctor --online
```

DeepSeek V4.1 Flash 的官方 API 名称是 `deepseek-flash`，见 [DeepSeek 官方文档](https://api-docs.deepseek.com/)。服务仍通过显式配置选择，不静默替换模型。`doctor --online` 会产生一次真实 API 调用；普通 doctor 不联网。

工具能力探测使用 `tool_choice=auto` 并检查实际返回的工具名及参数，兼容 DeepSeek 默认思考模式；该模式不接受强制指定工具，见 [接口说明](https://api-docs.deepseek.com/api/create-chat-completion/)。探测失败会明确停止，不改用其他模型。

## 使用

```powershell
# 裸运行直接进入交互会话
qagent
uv run qagent chat
uv run qagent ask '最近一周有哪些影响宽基ETF的事件？请列证据与反证。'
uv run qagent ask '分析这份材料，区分事实与作者观点' --file 'C:/Research/report.pdf'
uv run qagent backtest --symbols 510300 510500 159915 --start 2022-01-01 --end 2024-12-31 --lookback 60 --top-k 2 --rebalance weekly
uv run qagent memory add '我优先研究宽基ETF，关注回撤和换手成本'
uv run qagent memory pending
uv run qagent memory approve MEMORY_ID
uv run qagent runs list
```

示例ETF只是演示输入，不构成投资推荐。回测要求所有ETF在指定区间及热身期都有完整日线和可用交易日历；不满足时明确失败，不自动删除缺失日期。

全区间、前 70% 开发段、后 30% 留出段分别从现金开始。周轮动遇到区间首日不是调仓日时持币等待；等权买入持有基准在各段首日按每只 `1/N` 的前收盘目标买入，费用或跳空可造成部分成交。每份结果列出 0/5/10bp 成本比较与重复查看留出的标记。`runs show RUN_ID` 查看完整逐日持仓和成交记录。

数据默认保存到 `~/.qagent`，可用 `QAGENT_HOME` 指定目录。长期记忆、来源材料、实验和会话保存在本机，不随仓库上传；调用云端模型时，当前问题、相关已批准记忆和必要材料摘要会发送给配置的服务商。密钥不写入报告。

每次问题重新加载已批准记忆，优先最近修改项，最多传入 8000 字符，超过时明确标示截断。完整记忆仍可通过 `memory list` 查看。

## 验证

```powershell
uv run python -m unittest discover -s tests -v
uv run qagent --help
```

离线测试使用合成数据和假模型。实际测试与外部接口限制见 [docs/VALIDATION.md](docs/VALIDATION.md)。

## 研究限制

后复权小数单位、前收盘定量、次开盘同价顺序撮合与比例摩擦仅用于研究；不复现整手、最低佣金、分红到账、涨跌停排队与市场冲击。固定ETF池也有选择偏差。最新新闻只用于当前研究，不注入历史交易信号。目录和摘要不能冒充报告全文。
