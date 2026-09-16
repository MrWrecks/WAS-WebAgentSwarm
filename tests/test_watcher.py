import unittest

from web_agent_swarm.watcher import (
    _browser_event_requires_tick,
    _digest,
    _has_unresolved_work,
    _main_idle,
    _rollover_exhausted_workers,
    _scale_out_productive_work,
    _scale_in_zero_pressure_workers,
    _transport_alerts,
    _watch_snapshot,
)
from web_agent_swarm.watchdogs import conversation_limit_reached


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

    def test_zero_pressure_scale_in_closes_capacity_target(self):
        class Runtime:
            def __init__(self):
                self.scale_calls = []

            def elastic_plan(self, **kwargs):
                return {
                    "ok": True,
                    "active_instances": {"implementer": 1, "debugger": 0},
                    "coordination": {
                        "scale_recommendation": {
                            "recommended_instances": {"implementer": 0, "debugger": 0},
                        }
                    },
                }

            def scale(self, **kwargs):
                self.scale_calls.append(kwargs)
                return {"ok": True, "scaled": True, "removed_workers": ["implementer"]}

        runtime = Runtime()
        result = _scale_in_zero_pressure_workers(runtime)
        self.assertTrue(result["scaled"])
        self.assertEqual(runtime.scale_calls[0]["requested_instances"]["implementer"], 0)
        self.assertEqual(runtime.scale_calls[0]["scale_in_grace_seconds"], 0.0)
        self.assertFalse(runtime.scale_calls[0]["start_watch"])

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

    def test_verification_failure_wait_is_not_unresolved_worker_work(self):
        snapshot = self._snapshot()
        snapshot["tasks"] = [{
            "task_id": "verify-wait",
            "status": "blocked",
            "route": "wait_verification",
            "blocker_kind": "verification_failure",
        }]
        self.assertFalse(_has_unresolved_work(snapshot))

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

    def test_browser_event_tick_filter_ignores_focus_but_wakes_on_lifecycle(self):
        self.assertFalse(_browser_event_requires_tick({
            "kind": "tab_activated",
            "tabId": 7,
        }))
        self.assertFalse(_browser_event_requires_tick({
            "kind": "tab_lifecycle",
            "changes": {"autoDiscardable": False},
        }))
        self.assertTrue(_browser_event_requires_tick({
            "kind": "tab_lifecycle",
            "changes": {"frozen": True},
        }))
        self.assertTrue(_browser_event_requires_tick({
            "kind": "tab_removed",
            "tabId": 7,
        }))
        self.assertTrue(_browser_event_requires_tick({
            "kind": "extension_disconnected",
        }))

    def test_browser_suspended_is_transport_semantic(self):
        normal = self._snapshot()
        suspended = self._snapshot()
        suspended["agents"][1]["browser_state"] = "unresponsive"
        suspended["agents"][1]["ui"] = None
        suspended["agents"][1]["error"] = "Timed out waiting for extension response."
        self.assertNotEqual(_digest(normal), _digest(suspended))
        alerts = _transport_alerts(suspended)
        self.assertEqual(alerts[0]["kind"], "browser_suspended")
        self.assertEqual(alerts[0]["browser_state"], "unresponsive")
        self.assertTrue(_has_unresolved_work(suspended))

    def test_send_ack_timeout_is_transport_semantic_and_may_have_executed(self):
        snapshot = self._snapshot()
        snapshot["agents"][1]["binding"]["send_health"] = {
            "last_ok": False,
            "ack_timeout": True,
            "may_have_executed": True,
            "last_command_id": "cmd-worker",
            "last_error": "Timed out waiting for extension response.",
            "consecutive_failures": 1,
        }
        alerts = _transport_alerts(snapshot)
        self.assertEqual(alerts[0]["kind"], "send_ack_timeout")
        self.assertTrue(alerts[0]["may_have_executed"])
        self.assertEqual(alerts[0]["command_id"], "cmd-worker")

    def test_chatgpt_request_failure_is_transport_semantic(self):
        snapshot = self._snapshot({
            "generating": False,
            "uiState": "idle",
            "requestFailed": True,
        })
        alerts = _transport_alerts(snapshot)
        self.assertEqual(alerts[0]["kind"], "chatgpt_request_failed")

    def test_main_conversation_limit_is_transport_semantic(self):
        snapshot = self._snapshot()
        ui = {
            "generating": False,
            "sendButtonEnabled": False,
            "lastAssistantText": (
                "You have reached the maximum length for this conversation. "
                "Start a new chat to continue."
            ),
            "messages": [],
        }
        snapshot["agents"][0]["ui"] = ui
        self.assertTrue(conversation_limit_reached(ui))
        alerts = _transport_alerts(snapshot)
        self.assertEqual(alerts[0]["kind"], "main_conversation_limit")
        self.assertFalse(_main_idle(snapshot))

    def test_worker_conversation_limit_is_transport_semantic(self):
        snapshot = self._snapshot()
        snapshot["agents"][1]["ui"] = {
            "generating": False,
            "lastAssistantText": "You have reached the maximum length for this conversation.",
        }
        alerts = _transport_alerts(snapshot)
        self.assertEqual(alerts[0]["kind"], "worker_conversation_limit")
        self.assertEqual(alerts[0]["name"], "implementer")

    def test_worker_sweep_recovers_unresponsive_before_rollover(self):
        class Store:
            def bindings(self):
                return {"implementer": {"kind": "worker", "role": "implementer"}}

        class Bridge:
            def inspect(self, tab_id, tail=1):
                return {
                    "ok": False,
                    "error": "Timed out waiting for extension response.",
                    "browserState": "unresponsive",
                }

        class Runtime:
            def __init__(self):
                self.store = Store()
                self.bridge = Bridge()
                self.calls = []

            def _resolve(self, name):
                return {"id": 22}

            def _inspect_is_unresponsive(self, result):
                return True

            def recover_unresponsive_binding(self, name):
                self.calls.append(name)
                return {"ok": True, "reloaded": True, "recovered": True, "name": name}

            def rollover_worker_if_exhausted(self, name):
                raise AssertionError("unresponsive must refresh before rollover")

        runtime = Runtime()
        outcomes = _rollover_exhausted_workers(runtime)
        self.assertEqual(runtime.calls, ["implementer"])
        self.assertTrue(outcomes[0]["recovered"])

    def test_worker_rollover_sweep_checks_idle_bound_workers_too(self):
        class Store:
            def bindings(self):
                return {"implementer": {"kind": "worker", "role": "implementer"}}

        class Bridge:
            def inspect(self, tab_id, tail=1):
                return {
                    "ok": True,
                    "snapshot": {
                        "lastAssistantText": "You have reached the maximum length for this conversation."
                    },
                }

        class Runtime:
            def __init__(self):
                self.store = Store()
                self.bridge = Bridge()
                self.calls = []

            def _resolve(self, name):
                return {"id": 22}

            def rollover_worker_if_exhausted(self, name):
                self.calls.append(name)
                return {"ok": True, "rolled_over": True, "name": name}

        runtime = Runtime()
        outcomes = _rollover_exhausted_workers(runtime)
        self.assertEqual(runtime.calls, ["implementer"])
        self.assertTrue(outcomes[0]["rolled_over"])

    def test_empty_composer_with_disabled_send_button_is_still_idle_send_target(self):
        snapshot = self._snapshot()
        snapshot["agents"][0]["ui"] = {
            "generating": False,
            "composerPresent": True,
            "sendButtonEnabled": False,
            "conversationLimitReached": False,
        }
        self.assertTrue(_main_idle(snapshot))

    def test_suspended_main_is_not_treated_as_idle_send_target(self):
        snapshot = self._snapshot()
        snapshot["agents"][0]["browser_state"] = "frozen"
        snapshot["agents"][0]["ui"] = None
        snapshot["agents"][0]["error"] = "Timed out waiting for extension response."
        self.assertFalse(_main_idle(snapshot))

    def test_scale_out_productive_work_never_requests_removals(self):
        class Runtime:
            def __init__(self):
                self.scale_calls = []

            def elastic_plan(self, **kwargs):
                return {
                    "ok": True,
                    "active_instances": {
                        "implementer": 1,
                        "tester": 0,
                        "reviewer": 0,
                    },
                    "add_instances": [
                        {"role": "tester", "count": 1},
                        {"role": "reviewer", "count": 1},
                    ],
                    "remove_instances": [
                        {"role": "implementer", "count": 1},
                    ],
                }

            def scale(self, requested_instances=None, start_watch=True):
                self.scale_calls.append({
                    "requested_instances": requested_instances,
                    "start_watch": start_watch,
                })
                return {"ok": True, "scaled": True}

        runtime = Runtime()
        result = _scale_out_productive_work(runtime)
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["targets"],
            {"tester": 1, "reviewer": 1},
        )
        self.assertEqual(len(runtime.scale_calls), 1)
        self.assertEqual(
            runtime.scale_calls[0]["requested_instances"],
            {"tester": 1, "reviewer": 1},
        )
        self.assertFalse(runtime.scale_calls[0]["start_watch"])
        self.assertNotIn("implementer", runtime.scale_calls[0]["requested_instances"])

    def test_watch_snapshot_inspects_only_main(self):
        class Store:
            def bindings(self):
                return {
                    "main": {"kind": "main", "conversation_id": "main-c"},
                    "worker-a": {"kind": "worker", "role": "implementer"},
                    "worker-b": {"kind": "worker", "role": "tester"},
                }

            def tasks(self):
                return []

            def status(self):
                return {}

            def supersteps(self, include_pending=False):
                return []

            def interrupts(self):
                return []

            def graph_get(self):
                return {}

            def load(self):
                return {
                    "lanes": {},
                    "resource_claims": {},
                    "commits": {},
                    "canonical_epoch": 0,
                }

        class Bridge:
            def __init__(self):
                self.inspect_ids = []

            def inspect(self, tab_id, tail=2):
                self.inspect_ids.append(tab_id)
                return {
                    "ok": True,
                    "tab": {
                        "id": tab_id,
                        "discarded": False,
                        "frozen": False,
                        "autoDiscardable": False,
                    },
                    "snapshot": {"generating": False},
                }

        class Runtime:
            def __init__(self):
                self.store = Store()
                self.bridge = Bridge()
                self.resolved_name = None

            def _resolve(self, name):
                self.resolved_name = name
                return {
                    "id": 101,
                    "discarded": False,
                    "frozen": False,
                    "autoDiscardable": False,
                }

            def coordination_plan(self):
                return {"task_nodes": []}

            def _blocker_kind(self, task):
                return "unknown_blocker"

        runtime = Runtime()
        snapshot = _watch_snapshot(runtime)
        self.assertEqual(runtime.resolved_name, "main")
        self.assertEqual(runtime.bridge.inspect_ids, [101])
        self.assertEqual(
            [agent["name"] for agent in snapshot["agents"]],
            ["main", "worker-a", "worker-b"],
        )
        self.assertEqual(snapshot["agents"][1]["browser_state"], "unknown")


if __name__ == "__main__":
    unittest.main()
