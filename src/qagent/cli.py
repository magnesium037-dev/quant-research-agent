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


def main(argv=None):
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
            print("输入问题；/exit 退出。每次请求重新加载已批准记忆。")
            while True:
                try:
                    question = input("qagent> ").strip()
                except EOFError:
                    break
                if question in ("/exit", "/quit"):
                    break
                if question:
                    write_report(home, run_research(question, store, allowed_files=allowed))
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
