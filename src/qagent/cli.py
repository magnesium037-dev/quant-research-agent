"""User-only controls and terminal entry points."""
import argparse
import json
import os
import sys
from pathlib import Path

from .agent import backtest_report, clean, client_from_env, model_config, probe_tools, run_research


def parser():
    root = argparse.ArgumentParser(prog="qagent")
    sub = root.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--file", action="append", default=[])
    chat = sub.add_parser("chat")
    chat.add_argument("--file", action="append", default=[])
    chat.add_argument("--plain", action="store_true", help="使用简易行模式（管道输入自动启用）")
    for command in (ask, chat):
        command.add_argument("--no-thinking", action="store_true", help="隐藏接口返回的推理内容")
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--symbols", nargs="+", required=True)
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--lookback", type=int, choices=[20, 60, 120], default=60)
    backtest.add_argument("--top-k", type=int, choices=[1, 2], default=2)
    backtest.add_argument("--rebalance", choices=["daily", "weekly"], default="weekly")
    backtest.add_argument("--initial-cash", type=float, default=100000)
    memory = sub.add_parser("memory").add_subparsers(dest="action", required=True)
    add_memory = memory.add_parser("add")
    add_memory.add_argument("text")
    add_memory.add_argument("--category", choices=["preference", "project", "decision", "method", "constraint", "general"], default="general")
    edit = memory.add_parser("edit")
    edit.add_argument("id")
    edit.add_argument("text")
    for name in ("delete", "approve", "reject"):
        memory.add_parser(name).add_argument("id")
    for name in ("list", "pending"):
        memory.add_parser(name)
    runs = sub.add_parser("runs").add_subparsers(dest="action", required=True)
    runs.add_parser("list")
    runs.add_parser("show").add_argument("id")
    sub.add_parser("doctor").add_argument("--online", action="store_true")
    return root


def output(value):
    value = clean(value)
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))


def write_report(home, result, display=True):
    folder = home / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    # Store IDs must never become paths supplied by models.
    run_id = str(result["run_id"])
    if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in run_id):
        raise ValueError("无效的运行 ID")
    path = folder / f"{run_id}.md"
    path.write_text(clean(result["report"]), encoding="utf-8")
    if display:
        output(result["report"])
        print(f"\n运行: {run_id} | {result['status']} | {path}")


def doctor(online=False):
    config = model_config()
    presence = {"LLM_BASE_URL": bool(config["base_url"]), "LLM_MODEL": bool(config["model"]),
                "LLM_API_KEY": bool(config["api_key"])}
    output({"python": sys.version.split()[0], "configuration_present": presence,
            "configuration_source": config["source"], "model": config["model"]})
    if not all(presence.values()):
        return 1
    if online:
        client = client_from_env()
        try:
            probe_tools(client, config["model"])
        finally:
            client.close()
        output("工具调用能力验证成功。")
    return 0


def _chat_help():
    print("命令: /new 新会话 | /sessions 列出会话 | /resume ID 切换会话 | /open ID 查看报告 | /context 查看上轮注入 | /memory 待确认记忆 | /approve ID | /reject ID | /thinking on|off | /exit")


def _chat_sessions(store):
    runs = store.list_runs()
    if not runs:
        print("暂无历史会话。")
        return
    print("最近会话:")
    for run in runs[:12]:
        question = run["question"].replace("\n", " ")[:52]
        print(f"  {run['id'][:8]}  {run['status']:<9}  {question}")


def _chat_run(store, prefix):
    matches = [run for run in store.list_runs() if run["id"].startswith(prefix)]
    if len(matches) != 1:
        raise ValueError("会话 ID 不唯一或不存在")
    return store.get_run(matches[0]["id"])


def _chat_open(store, prefix):
    run = _chat_run(store, prefix)
    result = run.get("result") or {}
    report = result.get("report") if isinstance(result, dict) else None
    print(report or json.dumps(result, ensure_ascii=False, indent=2))
    return run


def main(argv=None):
    # Like Reasonix, a bare executable opens the interactive session. Explicit
    # subcommands remain available for scripts and automation.
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["chat"]
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "doctor":
            return doctor(args.online)
        from .store import Store
        home = Path(os.environ.get("QAGENT_HOME", str(Path.home() / ".qagent"))).expanduser().resolve()
        store = Store(home)
        if args.command in ("ask", "chat"):
            allowed = [str(Path(p).expanduser().resolve(strict=True)) for p in args.file]
            if args.command == "chat" and not args.plain and sys.stdin.isatty() and sys.stdout.isatty():
                from .tui import ResearchApp
                ResearchApp(home, allowed, show_thinking=not args.no_thinking).run()
                return 0
            show_thinking = not args.no_thinking
            def progress(kind, value):
                if kind == "context":
                    output(f"[上下文] 模型 {value['model']}；已批准记忆 {value['memory_characters']} 字符"
                           f"（截断：{value['memory_truncated']}）；前轮摘录 {value['previous_characters']} 字符"
                           f"（截断：{value['previous_truncated']}）；商业方法 {value.get('business_framework_characters', 0)} 字符；"
                           f"待确认记忆 {value.get('pending_memory_count', 0)} 条；授权文件 {value['authorized_files']} 个；工具 {len(value['tools'])} 个。")
                elif kind == "request":
                    output(f"[请求 {value['round']}] 消息 {value['characters']} 字符（非 token 数）；工具结果 {value['tool_results']} 条；等待模型…")
                elif kind == "reasoning" and show_thinking:
                    output("[模型推理 · 接口返回内容，非核验结论]\n" + value)
                elif kind == "tool":
                    output("[调用工具] " + value)
            if args.command == "ask":
                result = run_research(args.question, store, allowed_files=allowed, on_progress=progress)
                write_report(home, result)
                return 0 if result["status"] == "completed" else 1
            active = None
            print("┌─ qagent 研究会话 ───────────────────────────────────────┐")
            print("│ 输入问题，或输入 /help 查看会话命令。                    │")
            print("└──────────────────────────────────────────────────────────┘")
            config = model_config()
            output(f"模型：{config['model']} | 配置来源：{config['source']}")
            print("/context 查看上轮初始上下文；/thinking off 隐藏推理。推理在每次模型请求完成后显示。")
            while True:
                try:
                    label = active["id"][:8] if active else "new"
                    question = input(f"qagent [{label}]> ").strip()
                except EOFError:
                    break
                if question in ("/exit", "/quit"):
                    break
                if not question:
                    continue
                if question in ("/help", "/?"):
                    _chat_help()
                    continue
                if question == "/new":
                    active = None
                    print("已切换到新会话。")
                    continue
                if question == "/sessions":
                    _chat_sessions(store)
                    continue
                if question == "/context":
                    output((active.get("result") or {}).get("context", "这条旧记录未保存上下文。") if active else "尚无请求；首轮发送后可查看。")
                    continue
                if question == "/memory":
                    output(store.memory_list("pending"))
                    continue
                if question.startswith(("/approve ", "/reject ")):
                    command, prefix = question.split(None, 1)
                    matches = [item for item in store.memory_list("pending") if item["id"].startswith(prefix.strip())]
                    if len(matches) != 1:
                        raise ValueError("待确认记忆 ID 不存在或不唯一")
                    output((store.memory_approve if command == "/approve" else store.memory_reject)(matches[0]["id"]))
                    continue
                if question in ("/thinking on", "/thinking off"):
                    show_thinking = question.endswith(" on")
                    print("推理显示已" + ("开启。" if show_thinking else "关闭。"))
                    continue
                if question.startswith("/open "):
                    active = _chat_open(store, question.split(None, 1)[1].strip())
                    continue
                if question.startswith("/resume "):
                    active = _chat_open(store, question.split(None, 1)[1].strip())
                    print(f"已切换到会话 {active['id'][:8]}；下一条问题会带上该报告摘要。")
                    continue
                if question.startswith("/"):
                    print("未知命令，输入 /help 查看可用命令。")
                    continue
                previous = None
                if active and isinstance(active.get("result"), dict):
                    previous = {"question": active["question"], "answer": active["result"].get("report", "")}
                result = run_research(question, store, allowed_files=allowed, previous=previous, on_progress=progress)
                active = store.get_run(result["run_id"])
                write_report(home, result)
        elif args.command == "memory":
            action = args.action
            if action in ("list", "pending"):
                result = store.memory_list(status="pending" if action == "pending" else "approved")
            elif action == "add":
                result = store.memory_add(clean(args.text), category=args.category)
            elif action == "edit":
                result = store.memory_edit(args.id, clean(args.text))
            else:
                result = getattr(store, f"memory_{action}")(args.id)
            output(result)
        elif args.command == "runs":
            output(store.list_runs() if args.action == "list" else store.get_run(args.id))
        elif args.command == "backtest":
            from .backtest import run_backtest
            params = {k: v for k, v in vars(args).items() if k != "command"}
            run_id = store.create_run("momentum_rotation", kind="backtest")
            try:
                result = run_backtest(**params, store=store, run_id=run_id)
                store.finish_run(run_id, clean(result), status="completed")
                write_report(home, {"run_id": run_id, "status": "completed", "report": backtest_report(result)})
            except BaseException as exc:
                store.finish_run(run_id, {"error": type(exc).__name__}, status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed")
                raise
        return 0
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
        print("错误: " + clean(detail), file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
