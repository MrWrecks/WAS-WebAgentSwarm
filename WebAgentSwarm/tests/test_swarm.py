import tempfile
import unittest
from pathlib import Path

from web_agent_swarm.state import DurableStore
from web_agent_swarm.swarm import SwarmRuntime, conversation_id


class FakeBridge:
    def __init__(self):
        self.tabs_data = []
        self.next_id = 10
        self.sent = []

    def health(self):
        return {"ok": True, "extensionClients": 1}

    def tabs(self):
        return {"ok": True, "tabs": list(self.tabs_data)}

    def open_tab(self, url="https://chatgpt.com/", active=False):
        self.next_id += 1
        tab = {
            "id": self.next_id,
            "windowId": 1,
            "index": len(self.tabs_data),
            "title": "ChatGPT",
            "url": url,
            "active": active,
        }
        self.tabs_data.append(tab)
        return {"ok": True, "tab": tab}

    def inspect(self, tab_id, tail=6):
        return {
            "ok": True,
            "snapshot": {
                "generating": False,
                "uiState": "idle",
                "messages": [],
            },
        }

    def send(self, tab_id, text, command_id=""):
        self.sent.append((tab_id, text))
        for idx, tab in enumerate(self.tabs_data):
            if tab["id"] == tab_id and "/c/" not in tab["url"]:
                self.tabs_data[idx] = dict(
                    tab,
                    url="https://chatgpt.com/c/conversation-{0}".format(tab_id),
                )
        return {"ok": True, "tabId": tab_id, "commandId": command_id}

    def close_tab(self, tab_id):
        before = len(self.tabs_data)
        self.tabs_data = [
            tab for tab in self.tabs_data
            if int(tab.get("id", -1)) != int(tab_id)
        ]
        return {
            "ok": len(self.tabs_data) < before,
            "tabId": int(tab_id),
        }


class SwarmRuntimeTest(unittest.TestCase):
    def test_transient_web_conversation_id_is_not_stable(self):
        self.assertEqual(
            conversation_id("https://chatgpt.com/c/WEB:temporary-id"),
            "",
        )
        self.assertEqual(
            conversation_id("https://chatgpt.com/c/6a92d549-4410-83e9-a368-af961df580c1"),
            "6a92d549-4410-83e9-a368-af961df580c1",
        )

    def test_plan_does_not_touch_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            swarm = SwarmRuntime(bridge=bridge, store=DurableStore(Path(tmp)))
            plan = swarm.plan(worker_count=2)
            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(bridge.tabs_data, [])
            self.assertEqual(bridge.sent, [])

    def test_explicit_worker_count_can_exceed_role_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            swarm = SwarmRuntime(bridge=bridge, store=DurableStore(Path(tmp)))
            plan = swarm.plan(worker_count=8)
            self.assertTrue(plan["ok"])
            self.assertEqual(plan["worker_count"], 8)
            self.assertEqual(
                sum(item["count"] for item in plan["instance_plan"]),
                8,
            )
            self.assertEqual(bridge.tabs_data, [])
            self.assertEqual(bridge.sent, [])

    def test_start_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            swarm = SwarmRuntime(bridge=bridge, store=DurableStore(Path(tmp)))
            result = swarm.start(["implementer", "tester"], start_watch=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["worker_count"], 2)
            self.assertEqual(result["total_web_agents"], 3)
            self.assertEqual(
                set(swarm.store.bindings()),
                {"main", "implementer", "tester"},
            )

    def test_initial_plan_and_start_support_same_role_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            store = DurableStore(Path(tmp))
            swarm = SwarmRuntime(bridge=bridge, store=store)
            store.task_fanout(
                items=["a", "b", "c", "d"],
                owner="implementer",
                group_id="initial-map",
            )

            plan = swarm.plan()
            self.assertEqual(plan["worker_count"], 4)
            self.assertEqual(
                plan["instance_plan"],
                [{"role": "implementer", "count": 4}],
            )
            self.assertEqual(bridge.tabs_data, [])

            started = swarm.start(
                ["implementer"],
                instances={"implementer": 4},
                start_watch=False,
            )
            self.assertTrue(started["ok"])
            self.assertEqual(started["worker_count"], 4)
            self.assertEqual(started["worker_instances"], {"implementer": 4})
            self.assertEqual(
                set(store.bindings()),
                {
                    "main",
                    "implementer",
                    "implementer-2",
                    "implementer-3",
                    "implementer-4",
                },
            )

    def test_scale_adds_workers_without_removing_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            swarm = SwarmRuntime(bridge=bridge, store=DurableStore(Path(tmp)))
            started = swarm.start(["implementer", "tester"], start_watch=False)
            self.assertTrue(started["ok"])
            scaled = swarm.scale(
                requested_roles=["reviewer"],
                start_watch=False,
            )
            self.assertTrue(scaled["ok"])
            self.assertTrue(scaled["scaled"])
            self.assertEqual(
                set(swarm.store.bindings()),
                {"main", "implementer", "tester", "reviewer"},
            )
            self.assertEqual(scaled["added_roles"], ["reviewer"])

    def test_coordination_plan_routes_dependencies_and_blocked_interrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            store = DurableStore(Path(tmp))
            swarm = SwarmRuntime(bridge=bridge, store=store)
            store.task_create(
                "Research first",
                owner="researcher",
                task_id="research",
            )
            store.task_create(
                "Implement after research",
                owner="implementer",
                task_id="implement",
                dependencies=["research"],
            )

            waiting = swarm.coordination_plan(
                active_roles=["implementer"],
                max_new_roles=2,
            )
            implement = next(
                item for item in waiting["task_nodes"]
                if item["task_id"] == "implement"
            )
            self.assertEqual(implement["route"], "wait_dependencies")
            barrier = next(
                item for item in waiting["fan_in_barriers"]
                if item["task_id"] == "implement"
            )
            self.assertEqual(barrier["unresolved"], ["research"])
            self.assertEqual(
                waiting["scale_recommendation"]["add_roles"],
                ["researcher"],
            )

            store.task_update("research", status="done", summary="evidence ready")
            ready = swarm.coordination_plan(
                active_roles=["implementer"],
                max_new_roles=2,
            )
            implement = next(
                item for item in ready["task_nodes"]
                if item["task_id"] == "implement"
            )
            self.assertEqual(implement["route"], "dispatch")
            self.assertEqual(ready["scale_recommendation"]["add_roles"], [])

            store.task_update(
                "implement",
                status="blocked",
                summary="needs debugging",
            )
            blocked = swarm.coordination_plan(
                active_roles=["implementer"],
                max_new_roles=2,
            )
            interrupt = next(
                item for item in blocked["interrupts"]
                if item["task_id"] == "implement"
            )
            self.assertEqual(interrupt["role"], "debugger")
            self.assertEqual(
                blocked["scale_recommendation"]["add_roles"],
                ["debugger"],
            )

    def test_elastic_scale_uses_graph_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            store = DurableStore(Path(tmp))
            swarm = SwarmRuntime(bridge=bridge, store=store)
            started = swarm.start(["implementer"], start_watch=False)
            self.assertTrue(started["ok"])
            store.task_create(
                "Implementation task",
                owner="implementer",
                task_id="work",
            )
            store.task_update("work", status="blocked", summary="debug needed")

            elastic = swarm.elastic_plan(max_new_roles=1)
            self.assertEqual(elastic["add_roles"], ["debugger"])
            scaled = swarm.scale(max_new_roles=1, start_watch=False)
            self.assertTrue(scaled["ok"])
            self.assertTrue(scaled["scaled"])
            self.assertEqual(scaled["added_roles"], ["debugger"])
            self.assertEqual(
                set(store.bindings()),
                {"main", "implementer", "debugger"},
            )

    def test_external_wait_does_not_spawn_debugger_pressure(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            store = DurableStore(Path(tmp))
            swarm = SwarmRuntime(bridge=bridge, store=store)
            store.task_create(
                "Wait for extension",
                owner="implementer",
                task_id="wait-extension",
            )
            store.task_update(
                "wait-extension",
                status="blocked",
                summary="external_wait: waiting for browser extension",
            )

            plan = swarm.coordination_plan(
                active_roles=["implementer"],
                max_new_roles=2,
            )
            node = next(
                item for item in plan["task_nodes"]
                if item["task_id"] == "wait-extension"
            )
            self.assertEqual(node["route"], "wait_external")
            self.assertEqual(node["blocker_kind"], "external_wait")
            self.assertNotIn("debugger", plan["scale_recommendation"]["add_roles"])

    def test_elastic_scale_in_suspends_and_closes_idle_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            store = DurableStore(Path(tmp))
            swarm = SwarmRuntime(bridge=bridge, store=store)
            started = swarm.start(["implementer", "tester"], start_watch=False)
            self.assertTrue(started["ok"])

            plan = swarm.elastic_plan()
            self.assertTrue(plan["needs_scale_in"])
            scaled = swarm.scale(start_watch=False)
            self.assertTrue(scaled["ok"])
            self.assertTrue(scaled["scaled"])
            self.assertEqual(set(scaled["removed_workers"]), {"implementer", "tester"})
            self.assertEqual(scaled["worker_count"], 0)
            bindings = store.bindings()
            self.assertTrue(bindings["implementer"]["suspended"])
            self.assertTrue(bindings["tester"]["suspended"])
            self.assertEqual(
                [tab for tab in bridge.tabs_data if "/c/" in str(tab.get("url") or "")],
                [next(tab for tab in bridge.tabs_data if tab["id"] == 11)],
            )

    def test_scale_in_refuses_worker_with_live_task_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            store = DurableStore(Path(tmp))
            swarm = SwarmRuntime(bridge=bridge, store=store)
            started = swarm.start(["implementer"], start_watch=False)
            self.assertTrue(started["ok"])
            store.task_create(
                "leased work",
                owner="implementer",
                task_id="leased-work",
            )
            store.task_claim("leased-work", "implementer", lease_seconds=600)

            scaled = swarm.scale(
                requested_instances={"implementer": 0},
                start_watch=False,
            )
            self.assertFalse(scaled["ok"])
            self.assertEqual(scaled["removed_workers"], [])
            self.assertTrue(scaled["scale_in_blocked"])
            self.assertEqual(scaled["worker_instances"]["implementer"], 1)
            self.assertFalse(store.bindings()["implementer"].get("suspended", False))

    def test_send_style_fanout_scales_multiple_instances_of_same_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            store = DurableStore(Path(tmp))
            swarm = SwarmRuntime(bridge=bridge, store=store)
            started = swarm.start(["implementer"], start_watch=False)
            self.assertTrue(started["ok"])
            fanout = store.task_fanout(
                items=["a", "b", "c", "d"],
                owner="implementer",
                title_prefix="Shard",
                group_id="same-role-map",
            )
            self.assertEqual(len(fanout["task_ids"]), 4)

            elastic = swarm.elastic_plan(
                max_new_roles=3,
                max_instances_per_role=4,
            )
            implementer_add = next(
                item for item in elastic["add_instances"]
                if item["role"] == "implementer"
            )
            self.assertEqual(implementer_add["count"], 3)

            scaled = swarm.scale(
                max_new_roles=3,
                max_instances_per_role=4,
                start_watch=False,
            )
            self.assertTrue(scaled["ok"])
            self.assertEqual(
                scaled["added_workers"],
                ["implementer-2", "implementer-3", "implementer-4"],
            )
            self.assertEqual(scaled["worker_instances"]["implementer"], 4)
            self.assertEqual(
                set(store.bindings()),
                {
                    "main",
                    "implementer",
                    "implementer-2",
                    "implementer-3",
                    "implementer-4",
                },
            )


if __name__ == "__main__":
    unittest.main()
