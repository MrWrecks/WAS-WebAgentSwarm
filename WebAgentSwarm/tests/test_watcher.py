import unittest

from web_agent_swarm.watcher import _digest, _has_unresolved_work


class WatcherSemanticsTest(unittest.TestCase):
    def _snapshot(self, ui=None):
        return {
            "agents": [
                {
                    "name": "main",
                    "binding": {"kind": "main"},
                    "open": True,
                    "ui": {"generating": False},
                    "error": None,
                },
                {
                    "name": "implementer",
                    "binding": {"kind": "worker", "role": "implementer"},
                    "open": True,
                    "ui": ui or {"generating": False, "uiState": "idle"},
                    "error": None,
                },
            ],
            "tasks": [],
            "shared_status": {},
            "supersteps": [],
        }

    def test_ui_only_changes_do_not_change_semantic_digest(self):
        idle = self._snapshot({"generating": False, "uiState": "idle", "lastAssistantText": "a"})
        busy = self._snapshot({"generating": True, "uiState": "generating", "lastAssistantText": "b"})
        self.assertEqual(_digest(idle), _digest(busy))

    def test_stuck_generating_is_transport_semantic(self):
        normal = self._snapshot({"generating": True, "stuckGenerating": False})
        stuck = self._snapshot({"generating": True, "stuckGenerating": True})
        self.assertNotEqual(_digest(normal), _digest(stuck))

    def test_silence_wake_requires_unresolved_work(self):
        snapshot = self._snapshot()
        self.assertFalse(_has_unresolved_work(snapshot))
        snapshot["tasks"] = [{
            "task_id": "work",
            "status": "ready",
            "retry_after": 0,
        }]
        self.assertTrue(_has_unresolved_work(snapshot))


if __name__ == "__main__":
    unittest.main()
