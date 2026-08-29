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

    def test_open_interrupt_is_unresolved_work(self):
        snapshot = self._snapshot()
        snapshot["interrupts"] = [{
            "interrupt_id": "int-1",
            "status": "open",
            "prompt": "approve?",
        }]
        self.assertTrue(_has_unresolved_work(snapshot))

    def test_pending_scale_in_grace_is_capacity_maintenance_work(self):
        snapshot = self._snapshot()
        snapshot["agents"][1]["binding"]["excess_since"] = 1.0
        self.assertTrue(_has_unresolved_work(snapshot))

        snapshot["agents"][1]["binding"]["suspended"] = True
        self.assertFalse(_has_unresolved_work(snapshot))

    def test_active_graph_revision_changes_semantic_digest(self):
        first = self._snapshot()
        second = self._snapshot()
        first["active_graph"] = {"graph_id": "g", "revision": 1, "nodes": {}, "edges": {}}
        second["active_graph"] = {"graph_id": "g", "revision": 2, "nodes": {}, "edges": {}}
        self.assertNotEqual(_digest(first), _digest(second))

    def test_unselected_graph_branch_is_not_unresolved_work(self):
        snapshot = self._snapshot()
        snapshot["tasks"] = [{
            "task_id": "unused-branch",
            "status": "ready",
            "route": "graph_unselected",
            "retry_after": 0,
        }]
        self.assertFalse(_has_unresolved_work(snapshot))


if __name__ == "__main__":
    unittest.main()
