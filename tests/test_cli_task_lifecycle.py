"""Token-free CLI task lifecycle using the existing durable WAS store."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from web_agent_swarm.cli import main


class TaskCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="was-cli-task-")
        self.env = patch.dict(os.environ, {"WEB_SWARM_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(list(args)), 0)
        return json.loads(out.getvalue())

    def test_create_claim_heartbeat_complete_and_filter(self):
        task = self.cli("task-create", "Check repository health", "--owner", "tester")
        task_id = task["task_id"]
        self.assertEqual(self.cli("task-list", "--status", "ready")[0]["task_id"], task_id)

        claimed = self.cli("task-claim", task_id, "rdc-operator", "--lease-seconds", "30")
        self.assertEqual(claimed["status"], "active")
        with self.assertRaisesRegex(RuntimeError, "already leased"):
            self.cli("task-claim", task_id, "another-worker")
        touched = self.cli("task-heartbeat", task_id, "rdc-operator", "--lease-seconds", "30")
        self.assertEqual(touched["claimed_by"], "rdc-operator")

        result = self.cli("task-release", task_id, "rdc-operator",
                          "--summary", "Verified without an AI model",
                          "--result-json", '{"checks": 1}')
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["result"], {"checks": 1})
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(self.cli("task-list", "--status", "active")), 0)
        self.assertEqual(self.cli("task-list", "--status", "done")[0]["task_id"], task_id)
        with self.assertRaisesRegex(RuntimeError, "not leased"):
            self.cli("task-release", task_id, "rdc-operator")

    def test_invalid_result_json_does_not_release_claim(self):
        task = self.cli("task-create", "Preserve state")
        task_id = task["task_id"]
        self.cli("task-claim", task_id, "rdc-operator")
        with self.assertRaises(json.JSONDecodeError):
            self.cli("task-release", task_id, "rdc-operator", "--result-json", "{bad")
        self.assertEqual(self.cli("task-list", "--status", "active")[0]["task_id"], task_id)

    def test_dependency_and_terminal_failure(self):
        first = self.cli("task-create", "First step")
        second = self.cli("task-create", "Second step", "--depends-on", first["task_id"])
        with self.assertRaisesRegex(RuntimeError, "dependencies"):
            self.cli("task-claim", second["task_id"], "rdc-operator")
        self.cli("task-claim", first["task_id"], "rdc-operator")
        self.cli("task-release", first["task_id"], "rdc-operator",
                 "--status", "failed", "--no-retry", "--error", "Safe stop")
        self.assertEqual(self.cli("task-list", "--status", "failed")[0]["task_id"], first["task_id"])
        with self.assertRaisesRegex(RuntimeError, "dependencies"):
            self.cli("task-claim", second["task_id"], "rdc-operator")


if __name__ == "__main__":
    unittest.main()
