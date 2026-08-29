import tempfile
import unittest
from pathlib import Path

from web_agent_swarm.state import DurableStore


class DurableStoreTest(unittest.TestCase):
    def test_task_and_status_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            task = store.task_create("Build feature", owner="implementer")
            updated = store.task_update(task["task_id"], status="active", summary="started")
            self.assertEqual(updated["status"], "active")
            self.assertEqual(store.tasks()[0]["summary"], "started")

            store.status_set("build", "running")
            self.assertEqual(store.status()["build"]["value"], "running")


if __name__ == "__main__":
    unittest.main()
