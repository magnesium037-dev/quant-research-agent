"""Local, transactional research records. No model-facing approval API."""

import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError("文本须为 1–12000 字符")
    return value


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.home / "research.db", timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('approved','pending','rejected')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, question TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                params TEXT NOT NULL, snapshot_id TEXT REFERENCES snapshots(id),
                result TEXT NOT NULL, status TEXT NOT NULL,
                holdout_start TEXT, holdout_end TEXT,
                holdout_previously_viewed INTEGER NOT NULL, created_at TEXT NOT NULL
            );
        """)

    def _memory(self, memory_id):
        row = self.connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise ValueError("记忆不存在")
        return dict(row)

    def memory_add(self, text, source="user", pending=False):
        text, source = _text(text), _text(source)
        if type(pending) is not bool:
            raise ValueError("pending 必须为布尔值")
        memory_id, now = uuid4().hex, _now()
        with self.connection:
            self.connection.execute("INSERT INTO memories VALUES (?,?,?,?,?,?)",
                (memory_id, text, source, "pending" if pending else "approved", now, now))
        return self._memory(memory_id)

    def memory_list(self, status="approved"):
        if status not in ("approved", "pending", "rejected"):
            raise ValueError("无效记忆状态")
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM memories WHERE status=? ORDER BY created_at,id", (status,))]

    def memory_edit(self, memory_id, text):
        text = _text(text)
        with self.connection:
            cursor = self.connection.execute("UPDATE memories SET text=?,updated_at=? WHERE id=?",
                (text, _now(), memory_id))
            if not cursor.rowcount:
                raise ValueError("记忆不存在")
        return self._memory(memory_id)

    def memory_delete(self, memory_id):
        with self.connection:
            cursor = self.connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return {"id": memory_id, "deleted": bool(cursor.rowcount)}

    def _decide_memory(self, memory_id, status):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            memory = self._memory(memory_id)
            if memory["status"] == status:
                return memory
            if memory["status"] != "pending":
                raise ValueError("仅待确认记忆可以批准或拒绝")
            self.connection.execute("UPDATE memories SET status=?,updated_at=? WHERE id=?",
                (status, _now(), memory_id))
            return self._memory(memory_id)

    def memory_approve(self, memory_id):
        return self._decide_memory(memory_id, "approved")

    def memory_reject(self, memory_id):
        return self._decide_memory(memory_id, "rejected")

    def create_run(self, question, kind="research"):
        question, kind = _text(question), _text(kind)
        run_id, now = uuid4().hex, _now()
        with self.connection:
            self.connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,NULL)",
                (run_id, question, kind, "running", now, now))
        return run_id

    def event(self, run_id, kind, payload):
        kind, payload = _text(kind), _json(payload)
        with self.connection:
            self.connection.execute("INSERT INTO events(run_id,kind,payload,created_at) VALUES (?,?,?,?)",
                (run_id, kind, payload, _now()))

    def finish_run(self, run_id, result, status="completed"):
        if status not in ("completed", "partial", "failed", "cancelled"):
            raise ValueError("无效运行状态")
        result = _json(result)
        with self.connection:
            cursor = self.connection.execute("UPDATE runs SET result=?,status=?,updated_at=? WHERE id=?",
                (result, status, _now(), run_id))
            if not cursor.rowcount:
                raise ValueError("运行不存在")

    def list_runs(self):
        return [dict(row) for row in self.connection.execute(
            "SELECT id,question,kind,status,created_at,updated_at FROM runs ORDER BY created_at DESC,id")]

    def get_run(self, run_id):
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("运行不存在")
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] is not None else None
        result["events"] = [dict(row) for row in self.connection.execute(
            "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]
        for event in result["events"]:
            event["payload"] = json.loads(event["payload"])
        result["experiments"] = [self._experiment(row) for row in self.connection.execute(
            "SELECT * FROM experiments WHERE run_id=? ORDER BY created_at,id", (run_id,))]
        return result

    def save_snapshot(self, payload):
        payload = _json(payload)
        snapshot_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?)",
                (snapshot_id, payload, _now()))
        return snapshot_id

    def get_snapshot(self, snapshot_id):
        row = self.connection.execute("SELECT payload FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise ValueError("快照不存在")
        if hashlib.sha256(row["payload"].encode("utf-8")).hexdigest() != snapshot_id:
            raise ValueError("快照哈希校验失败")
        return json.loads(row["payload"])

    @staticmethod
    def _experiment(row):
        record = dict(row)
        record["params"] = json.loads(record["params"])
        record["result"] = json.loads(record["result"])
        record["holdout_previously_viewed"] = bool(record["holdout_previously_viewed"])
        return record

    def record_experiment(self, run_id, params, snapshot_id, result):
        params = _json(params)
        result = json.loads(_json(result))
        status = result.get("status")
        if status not in ("completed", "failed"):
            raise ValueError("实验状态须为 completed 或 failed")
        start = end = None
        if status == "completed":
            start = date.fromisoformat(result["holdout_start"]).isoformat()
            end = date.fromisoformat(result["holdout_end"]).isoformat()
            if start > end or not snapshot_id:
                raise ValueError("成功实验需要快照和有效留出区间")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            viewed = bool(start and self.connection.execute(
                "SELECT 1 FROM experiments WHERE status='completed' AND holdout_start<=? AND holdout_end>=? LIMIT 1",
                (end, start)).fetchone())
            result["holdout_previously_viewed"] = viewed
            experiment_id = uuid4().hex
            self.connection.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?)",
                (experiment_id, run_id, params, snapshot_id, _json(result), status, start, end, int(viewed), _now()))
            return self._experiment(self.connection.execute(
                "SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone())

    def close(self):
        self.connection.close()
