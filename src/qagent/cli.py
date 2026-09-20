"""User-only controls and terminal entry points."""
import argparse
import json
import os
import sys
from pathlib import Path

from .agent import backtest_report, clean, client_from_env, probe_tools, run_research


def parser():
    root = argparse.ArgumentParser(prog="qagent")
    sub = root.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--file", action="append", default=[])
    chat = sub.add_parser("chat")
    chat.add_argument("--file", action="append", default=[])
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--symbols", nargs="+", required=True)
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--lookback", type=int, choices=[20, 60, 120], default=60)
    backtest.add_argument("--top-k", type=int, choices=[1, 2], default=2)
    backtest.add_argument("--rebalance", choices=["daily", "weekly"], default="weekly")
    backtest.add_argument("--initial-cash", type=float, default=100000)
    memory = sub.add_parser("memory").add_subparsers(dest="action", required=True)
    memory.add_parser("add").add_argument("text")
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


def write_report(home, result):
    folder = home / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    # Store IDs must never become paths supplied by models.
    run_id = str(result["run_id"])
    if not run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in run_id):
        raise ValueError("无效的运行 ID")
    path = folder / f"{run_id}.md"
    path.write_text(clean(result["report"]), encoding="utf-8")
    output(result["report"])
    print(f"\n运行: {run_id} | {result['status']} | {path}")


def doctor(online=False):
    presence = {key: bool(os.environ.get(key)) for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
    output({"python": sys.version.split()[0], "configuration_present": presence})
    if not all(presence.values()):
        return 1
    if online:
        client = client_from_env()
        try:
            probe_tools(client, os.environ["LLM_MODEL"])
        finally:
            client.close()
        output("工具调用能力验证成功。")
    return 0


def _chat_help():
    print("命令: /new 新会话 | /sessions 列出会话 | /resume ID 切换会话 | /open ID 查看报告 | /exit 退出")


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
            if args.command == "ask":
                result = run_research(args.question, store, allowed_files=allowed)
                write_report(home, result)
                return 0 if result["status"] == "completed" else 1
            active = None
            print("┌─ qagent 研究会话 ───────────────────────────────────────┐")
            print("│ 输入问题，或输入 /help 查看会话命令。                    │")
            print("└──────────────────────────────────────────────────────────┘")
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
                prompt = question
                if active and isinstance(active.get("result"), dict):
                    previous = active["result"].get("report", "")
                    if previous:
                        prompt = f"请继续此前会话。此前报告摘要如下：\n{previous[-6000:]}\n\n新的问题：{question}"
                result = run_research(prompt, store, allowed_files=allowed)
                active = store.get_run(result["run_id"])
                write_report(home, result)
        elif args.command == "memory":
            action = args.action
            if action in ("list", "pending"):
                result = store.memory_list(status="pending" if action == "pending" else "approved")
            elif action == "add":
                result = store.memory_add(clean(args.text))
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
