import tempfile
import unittest
from pathlib import Path

from web_agent_swarm.state import DurableStore
from web_agent_swarm.swarm import SwarmRuntime


class SwarmGraphTest(unittest.TestCase):
    def _store(self, tmp):
        return DurableStore(Path(tmp))

    def test_graph_crud_revision_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.task_create("A", owner="implementer", task_id="a")
            store.task_create("B", owner="tester", task_id="b")

            graph = store.graph_create("Flow", graph_id="flow")
            self.assertEqual(graph["revision"], 1)
            first = store.graph_node_add(
                "flow",
                "node-a",
                task_id="a",
                role="implementer",
                expected_revision=1,
            )
            self.assertEqual(first["revision"], 2)

            with self.assertRaises(RuntimeError):
                store.graph_node_add(
                    "flow",
                    "stale-node",
                    task_id="b",
                    expected_revision=1,
                )

            second = store.graph_node_add(
                "flow",
                "node-b",
                task_id="b",
                role="tester",
                expected_revision=2,
            )
            self.assertEqual(second["revision"], 3)
            edge = store.graph_edge_add(
                "flow",
                "node-a",
                "node-b",
                edge_id="a-to-b",
                expected_revision=3,
            )
            self.assertEqual(edge["revision"], 4)

            validation = store.graph_validate("flow")
            self.assertTrue(validation["ok"])
            self.assertEqual(validation["entry_nodes"], ["node-a"])
            self.assertEqual(validation["terminal_nodes"], ["node-b"])

            with self.assertRaises(RuntimeError):
                store.graph_edge_add(
                    "flow",
                    "node-b",
                    "node-a",
                    edge_id="cycle",
                    expected_revision=4,
                )
            self.assertEqual(store.graph_get("flow")["revision"], 4)

            active = store.graph_activate("flow", expected_revision=4)
            self.assertTrue(active["active"])

            reloaded = DurableStore(Path(tmp))
            self.assertTrue(reloaded.graph_get("flow")["active"])
            self.assertEqual(reloaded.graph_get("flow")["edges"]["a-to-b"]["source"], "node-a")

    def test_dependency_graph_gates_plan_and_atomic_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.task_create("First", owner="implementer", task_id="first")
            store.task_create("Second", owner="implementer", task_id="second")
            store.graph_create("Dependency", graph_id="dep")
            store.graph_node_add("dep", "n1", task_id="first", role="implementer")
            store.graph_node_add("dep", "n2", task_id="second", role="tester")
            store.graph_edge_add("dep", "n1", "n2", edge_id="dep-edge")
            store.graph_activate("dep")

            swarm = SwarmRuntime(store=store)
            plan = swarm.coordination_plan(active_roles=[])
            self.assertEqual(plan["model"], "durable_swarm_graph")
            nodes = {item["task_id"]: item for item in plan["task_nodes"]}
            self.assertEqual(nodes["first"]["route"], "dispatch")
            self.assertEqual(nodes["second"]["route"], "wait_graph")
            self.assertEqual(nodes["second"]["role"], "tester")
            self.assertFalse(nodes["second"]["graph_gate"]["ready"])

            with self.assertRaises(RuntimeError):
                store.task_claim("second", "tester")

            store.task_claim("first", "implementer")
            store.task_release("first", "implementer", status="done", result={"ok": True})

            plan = swarm.coordination_plan(active_roles=[])
            nodes = {item["task_id"]: item for item in plan["task_nodes"]}
            self.assertEqual(nodes["second"]["route"], "dispatch")
            self.assertTrue(nodes["second"]["graph_gate"]["ready"])
            with self.assertRaises(RuntimeError):
                store.task_claim("second", "implementer")
            claimed = store.task_claim("second", "tester")
            self.assertEqual(claimed["claimed_by"], "tester")

    def test_conditional_graph_selects_one_branch_and_rejects_other_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.task_create("Decide", owner="researcher", task_id="decide")
            store.task_create("Fast path", owner="implementer", task_id="fast")
            store.task_create("Slow path", owner="implementer", task_id="slow")
            store.graph_create("Conditional", graph_id="cond")
            store.graph_node_add("cond", "router", task_id="decide", role="researcher")
            store.graph_node_add("cond", "fast-node", task_id="fast", role="tester")
            store.graph_node_add("cond", "slow-node", task_id="slow")
            store.graph_edge_add(
                "cond",
                "router",
                "fast-node",
                kind="conditional",
                edge_id="fast-edge",
                condition={
                    "source": "task_result",
                    "path": "route",
                    "op": "eq",
                    "value": "fast",
                },
            )
            store.graph_edge_add(
                "cond",
                "router",
                "slow-node",
                kind="conditional",
                edge_id="slow-edge",
                condition={
                    "source": "task_result",
                    "path": "route",
                    "op": "eq",
                    "value": "slow",
                },
            )
            store.graph_activate("cond")

            swarm = SwarmRuntime(store=store)
            before = {
                item["task_id"]: item
                for item in swarm.coordination_plan(active_roles=[])["task_nodes"]
            }
            self.assertEqual(before["fast"]["route"], "wait_graph")
            self.assertEqual(before["slow"]["route"], "wait_graph")

            store.task_claim("decide", "researcher")
            store.task_release(
                "decide",
                "researcher",
                status="done",
                result={"route": "fast"},
            )

            plan = swarm.coordination_plan(active_roles=[])
            nodes = {item["task_id"]: item for item in plan["task_nodes"]}
            self.assertEqual(nodes["fast"]["route"], "dispatch")
            self.assertEqual(nodes["fast"]["role"], "tester")
            self.assertEqual(nodes["slow"]["route"], "graph_unselected")
            selected = plan["swarm_graph"]["task_gates"]["fast"]
            rejected = plan["swarm_graph"]["task_gates"]["slow"]
            self.assertEqual(selected["selected_edge_ids"], ["fast-edge"])
            self.assertTrue(rejected["unselected"])

            with self.assertRaises(RuntimeError):
                store.task_claim("slow", "implementer")
            claimed = store.task_claim("fast", "tester")
            self.assertEqual(claimed["claimed_by"], "tester")

    def test_control_node_state_can_drive_conditional_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.task_create("Execute", owner="implementer", task_id="execute")
            store.graph_create("Control", graph_id="control")
            store.graph_node_add(
                "control",
                "router",
                kind="router",
                status="done",
                state={"choice": "go"},
            )
            store.graph_node_add("control", "work", task_id="execute")
            store.graph_edge_add(
                "control",
                "router",
                "work",
                kind="conditional",
                edge_id="route",
                condition={
                    "source": "source_state",
                    "path": "choice",
                    "op": "eq",
                    "value": "go",
                },
            )
            store.graph_activate("control")

            plan = SwarmRuntime(store=store).coordination_plan(active_roles=[])
            node = next(item for item in plan["task_nodes"] if item["task_id"] == "execute")
            self.assertEqual(node["route"], "dispatch")

    def test_graph_update_remove_and_live_lease_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.task_create("A", task_id="a")
            store.task_create("B", task_id="b")
            store.graph_create("Mutable", graph_id="mutable")
            store.graph_node_add("mutable", "a-node", task_id="a")
            store.graph_node_add("mutable", "b-node", task_id="b", role="tester")
            store.graph_activate("mutable")

            claimed = store.task_claim("b", "tester")
            self.assertEqual(claimed["claimed_by"], "tester")

            graph = store.graph_get("mutable")
            edge = store.graph_edge_add(
                "mutable",
                "a-node",
                "b-node",
                edge_id="late-gate",
                expected_revision=graph["revision"],
            )
            self.assertEqual(edge["edge"]["kind"], "dependency")

            reclaimed = store.task_claim("b", "tester")
            self.assertEqual(reclaimed["claimed_by"], "tester")

            store.task_release("b", "tester", status="done")
            updated = store.graph_node_update(
                "mutable",
                "a-node",
                {"role": "researcher", "label": "renamed"},
                expected_revision=edge["revision"],
            )
            self.assertEqual(updated["node"]["role"], "researcher")
            self.assertEqual(updated["node"]["label"], "renamed")

            edge_update = store.graph_edge_update(
                "mutable",
                "late-gate",
                {
                    "kind": "conditional",
                    "condition": {
                        "source": "source_status",
                        "op": "eq",
                        "value": "done",
                    },
                },
                expected_revision=updated["revision"],
            )
            self.assertEqual(edge_update["edge"]["kind"], "conditional")

            removed = store.graph_edge_remove(
                "mutable",
                "late-gate",
                expected_revision=edge_update["revision"],
            )
            self.assertEqual(removed["removed_edge_id"], "late-gate")

            current = store.graph_get("mutable")
            removed_node = store.graph_node_remove(
                "mutable",
                "b-node",
                expected_revision=current["revision"],
            )
            self.assertEqual(removed_node["removed_node_id"], "b-node")

            current = store.graph_get("mutable")
            with self.assertRaises(RuntimeError):
                store.graph_node_remove(
                    "mutable",
                    "a-node",
                    expected_revision=current["revision"],
                )

            store.graph_deactivate("mutable")
            removed_last = store.graph_node_remove(
                "mutable",
                "a-node",
                expected_revision=current["revision"],
            )
            self.assertEqual(removed_last["removed_node_id"], "a-node")

    def test_bulk_replace_and_preview_are_transactional(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.task_create("A", task_id="a")
            store.task_create("B", task_id="b")
            graph = store.graph_create("Bulk", graph_id="bulk")

            replaced = store.graph_replace(
                "bulk",
                nodes=[
                    {"node_id": "a-node", "task_id": "a", "role": "implementer"},
                    {"node_id": "b-node", "task_id": "b", "role": "tester"},
                ],
                edges=[
                    {
                        "edge_id": "a-b",
                        "source": "a-node",
                        "target": "b-node",
                        "kind": "dependency",
                    }
                ],
                expected_revision=graph["revision"],
            )
            self.assertEqual(replaced["revision"], 2)
            preview = store.graph_preview("bulk")
            self.assertTrue(preview["preview"])
            self.assertFalse(preview["active"])
            self.assertFalse(preview["task_gates"]["b"]["ready"])

            with self.assertRaises(RuntimeError):
                store.graph_replace(
                    "bulk",
                    nodes=[
                        {"node_id": "a-node", "task_id": "a"},
                        {"node_id": "b-node", "task_id": "b"},
                    ],
                    edges=[
                        {
                            "edge_id": "a-b",
                            "source": "a-node",
                            "target": "b-node",
                            "kind": "dependency",
                        },
                        {
                            "edge_id": "b-a",
                            "source": "b-node",
                            "target": "a-node",
                            "kind": "dependency",
                        },
                    ],
                    expected_revision=2,
                )
            self.assertEqual(store.graph_get("bulk")["revision"], 2)
            self.assertEqual(set(store.graph_get("bulk")["edges"]), {"a-b"})

            store.graph_activate("bulk", expected_revision=2)
            active_preview = store.graph_preview()
            self.assertTrue(active_preview["active"])
            self.assertEqual(active_preview["graph_id"], "bulk")

    def test_checkpoint_restores_graph_topology_and_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.task_create("A", task_id="a")
            store.graph_create("Original", graph_id="flow")
            store.graph_node_add("flow", "a-node", task_id="a")
            store.graph_activate("flow")
            checkpoint = store.checkpoint_create(label="graph checkpoint")

            store.graph_deactivate("flow")
            store.graph_update("flow", {"name": "Mutated"})
            self.assertEqual(store.graph_get("flow")["name"], "Mutated")
            self.assertFalse(store.graph_get("flow")["active"])

            restored = store.checkpoint_restore(checkpoint["checkpoint_id"])
            self.assertEqual(restored["active_graph_id"], "flow")
            graph = store.graph_get("flow")
            self.assertTrue(graph["active"])
            self.assertEqual(graph["name"], "Original")
            self.assertIn("a-node", graph["nodes"])


if __name__ == "__main__":
    unittest.main()
