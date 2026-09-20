import sqlite3
import tempfile
import unittest
from pathlib import Path

from qagent.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = Store(self.home)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_memory_approval_restart_edit_delete(self):
        pending = self.store.memory_add("低换手", source="agent:run", pending=True)
        self.assertEqual(self.store.memory_list(), [])
        approved = self.store.memory_approve(pending["id"])
        self.assertEqual(approved, self.store.memory_approve(pending["id"]))
        self.assertEqual(len(self.store.memory_list()), 1)
        self.store.close()
        self.store = Store(self.home)
        self.assertEqual(self.store.memory_list()[0]["source"], "agent:run")
        edited = self.store.memory_edit(pending["id"], "控制回撤")
        self.assertEqual(edited["created_at"], pending["created_at"])
        self.assertEqual(edited["text"], "控制回撤")
        self.assertTrue(self.store.memory_delete(pending["id"])["deleted"])
        self.assertFalse(self.store.memory_delete(pending["id"])["deleted"])

    def test_rejected_cannot_be_approved_and_edits_do_not_change_status(self):
        memory = self.store.memory_add("想法", pending=True)
        rejected = self.store.memory_reject(memory["id"])
        self.assertEqual(self.store.memory_reject(memory["id"]), rejected)
        with self.assertRaises(ValueError):
            self.store.memory_approve(memory["id"])
        self.store.memory_edit(memory["id"], "新想法")
        self.assertEqual(self.store.memory_list(), [])
        self.assertEqual(self.store.memory_list("rejected")[0]["text"], "新想法")

    def test_approval_failure_rolls_back(self):
        original = self.store.memory_add("保留", pending=True)
        self.store.connection.executescript("""
            CREATE TRIGGER fail_approval AFTER UPDATE ON memories
            WHEN NEW.status='approved' BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.memory_approve(original["id"])
        self.assertEqual(self.store.memory_list("pending"), [original])
        self.assertEqual(self.store.memory_list(), [])

    def test_durable_run_events_snapshot_and_invalid_write(self):
        run = self.store.create_run("问题")
        self.store.event(run, "tool", {"items": ["公开证据"]})
        snapshot = self.store.save_snapshot({"b": 2, "a": [1]})
        self.assertEqual(snapshot, self.store.save_snapshot({"a": [1], "b": 2}))
        self.assertNotEqual(snapshot, self.store.save_snapshot({"a": [2], "b": 2}))
        self.store.finish_run(run, {"report": "报告"}, "partial")
        with self.assertRaises(ValueError):
            self.store.finish_run(run, {"value": float("nan")})
        self.store.close()
        self.store = Store(self.home)
        record = self.store.get_run(run)
        self.assertEqual(record["result"], {"report": "报告"})
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["events"][0]["payload"], {"items": ["公开证据"]})
        self.assertEqual(self.store.get_snapshot(snapshot), {"a": [1], "b": 2})
        self.assertEqual(self.store.list_runs()[0]["id"], run)

    def test_overlap_is_global_independent_of_pool_params_and_snapshot(self):
        run = self.store.create_run("实验")
        snapshot = self.store.save_snapshot({"prices": [1, 2]})
        result = {"status": "completed", "holdout_start": "2024-01-01", "holdout_end": "2024-03-31"}
        first = self.store.record_experiment(run, {"lookback": 20}, snapshot, result)
        self.assertFalse(first["holdout_previously_viewed"])
        self.assertNotIn("holdout_previously_viewed", result)
        run2 = self.store.create_run("另一池")
        snapshot2 = self.store.save_snapshot({"prices": [3, 4]})
        overlap = dict(result, holdout_start="2024-03-31", holdout_end="2024-06-30")
        repeated = self.store.record_experiment(run2, {"lookback": 120}, snapshot2, overlap)
        self.assertTrue(repeated["holdout_previously_viewed"])
        self.assertTrue(repeated["result"]["holdout_previously_viewed"])
        fresh = dict(result, holdout_start="2024-07-01", holdout_end="2024-08-31")
        self.assertFalse(self.store.record_experiment(run2, {}, snapshot, fresh)["holdout_previously_viewed"])
        self.assertEqual(len(self.store.get_run(run2)["experiments"]), 2)

    def test_failed_attempts_retained_without_marking_holdout_or_memory(self):
        run = self.store.create_run("失败尝试")
        failed = {"status": "failed", "error": "缺少交易日", "holdout_start": "2024-01-01", "holdout_end": "2024-02-01"}
        record = self.store.record_experiment(run, {}, None, failed)
        self.assertIsNone(record["snapshot_id"])
        snapshot = self.store.save_snapshot({})
        success = dict(failed, status="completed")
        self.assertFalse(self.store.record_experiment(run, {}, snapshot, success)["holdout_previously_viewed"])
        self.assertEqual(len(self.store.get_run(run)["experiments"]), 2)
        self.assertEqual(self.store.memory_list(), [])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_experiment(run, {}, "missing", success)
        self.assertEqual(len(self.store.get_run(run)["experiments"]), 2)


if __name__ == "__main__":
    unittest.main()
