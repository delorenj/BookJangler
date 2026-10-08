import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from capture_usage import export_session_from_db, summarize_session


class UsageTests(unittest.TestCase):
    def test_excludes_unfinished_and_user_messages(self):
        data = {
            "info": {"id": "session", "parentID": "parent"},
            "messages": [
                {
                    "info": {
                        "role": "assistant",
                        "time": {"completed": 1},
                        "modelID": "model",
                        "providerID": "provider",
                        "cost": 0.5,
                        "tokens": {
                            "input": 7,
                            "output": 3,
                            "reasoning": 2,
                            "total": 20,
                            "cache": {"read": 10, "write": 0},
                        },
                    }
                },
                {"info": {"role": "assistant", "time": {}, "tokens": {"total": 999}}},
                {"info": {"role": "user", "tokens": {"total": 999}}},
            ],
        }
        result = summarize_session(data)
        self.assertEqual(result["reported_total_tokens"], 20)
        self.assertEqual(result["input_tokens"], 7)
        self.assertEqual(result["output_tokens"], 3)
        self.assertEqual(result["cache_read_tokens"], 10)
        self.assertEqual(result["reasoning_tokens"], 2)
        self.assertEqual(result["opencode_reported_cost_usd"], 0.5)
        self.assertEqual(result["completed_assistant_messages"], 1)
        self.assertEqual(result["incomplete_assistant_messages_excluded"], 1)
        self.assertEqual(result["parent_session_id"], "parent")

    def test_missing_total_is_flagged_not_estimated(self):
        data = {
            "info": {"id": "session"},
            "messages": [
                {
                    "info": {
                        "role": "assistant",
                        "time": {"completed": 1},
                        "tokens": {"input": 12, "output": 8},
                    }
                }
            ],
        }
        result = summarize_session(data)
        self.assertEqual(result["reported_total_tokens"], 0)
        self.assertEqual(result["messages_missing_total_tokens"], 1)
        self.assertEqual(result["input_tokens"], 12)

    def test_missing_usage_and_cost_are_flagged(self):
        data = {
            "info": {"id": "session"},
            "messages": [{"info": {"role": "assistant", "time": {"completed": 1}}}],
        }
        result = summarize_session(data)
        self.assertEqual(result["messages_missing_usage"], 1)
        self.assertEqual(result["messages_missing_cost"], 1)
        self.assertEqual(result["messages_missing_total_tokens"], 1)

    def test_empty_session_has_no_usage(self):
        result = summarize_session({"info": {"id": "empty"}, "messages": []})
        self.assertEqual(result["reported_total_tokens"], 0)
        self.assertEqual(result["completed_assistant_messages"], 0)
        self.assertEqual(result["models"], [])


class DbExportTests(unittest.TestCase):
    def test_rebuilds_export_shape_from_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "o.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                "create table session(id, parent_id, directory, title, time_created);"
                "create table message(id, session_id, time_created, data);"
                "create table part(id, message_id, session_id, data);"
            )
            conn.execute("insert into session values('s','p','/d','t',5)")
            conn.execute(
                "insert into message values('m1','s',1,?)",
                (json.dumps({"role": "assistant", "time": {"completed": 2}, "tokens": {"input": 4}}),),
            )
            conn.execute("insert into part values('x1','m1','s',?)", (json.dumps({"type": "text"}),))
            conn.commit()
            conn.close()
            data = export_session_from_db("s", path)
            self.assertEqual(data["info"]["parentID"], "p")
            self.assertEqual(data["messages"][0]["info"]["id"], "m1")
            self.assertEqual(data["messages"][0]["parts"][0]["id"], "x1")
            self.assertEqual(summarize_session(data)["input_tokens"], 4)
            with self.assertRaises(RuntimeError):
                export_session_from_db("missing", path)


if __name__ == "__main__":
    unittest.main()
