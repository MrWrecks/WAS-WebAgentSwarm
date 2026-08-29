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

    def test_task_dependencies_are_durable_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            first = store.task_create(
                "Research",
                owner="researcher",
                task_id="research",
            )
            second = store.task_create(
                "Implement",
                owner="implementer",
                task_id="implement",
                dependencies=[first["task_id"]],
            )
            self.assertEqual(second["dependencies"], ["research"])
            self.assertEqual(
                next(item for item in store.tasks() if item["task_id"] == "implement")[
                    "dependencies"
                ],
                ["research"],
            )
            with self.assertRaises(ValueError):
                store.task_create(
                    "Invalid",
                    task_id="invalid",
                    dependencies=["missing"],
                )

    def test_claim_lease_retry_and_reduce(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            fanout = store.task_fanout(
                items=[{"value": 1}, {"value": 2}, {"value": 3}],
                owner="implementer",
                title_prefix="Map",
                group_id="map-group",
                max_attempts=2,
            )
            self.assertEqual(len(fanout["task_ids"]), 3)

            first = fanout["task_ids"][0]
            claimed = store.task_claim(first, "implementer", lease_seconds=60)
            self.assertEqual(claimed["claimed_by"], "implementer")
            self.assertEqual(claimed["attempts"], 1)
            with self.assertRaises(RuntimeError):
                store.task_claim(first, "implementer-2", lease_seconds=60)

            retried = store.task_release(
                first,
                "implementer",
                status="failed",
                error="transient",
                retryable=True,
            )
            self.assertEqual(retried["status"], "ready")
            reclaimed = store.task_claim(first, "implementer-2", lease_seconds=60)
            self.assertEqual(reclaimed["attempts"], 2)
            store.task_release(
                first,
                "implementer-2",
                status="done",
                result={"mapped": 10},
            )

            for index, task_id in enumerate(fanout["task_ids"][1:], start=2):
                worker = "implementer-{0}".format(index + 1)
                store.task_claim(task_id, worker, lease_seconds=60)
                store.task_release(
                    task_id,
                    worker,
                    status="done",
                    result={"mapped": index * 10},
                )

            reduced = store.task_reduce("map-group")
            self.assertTrue(reduced["complete"])
            self.assertFalse(reduced["failed"])
            self.assertEqual(
                reduced["results"],
                [{"mapped": 10}, {"mapped": 20}, {"mapped": 30}],
            )


    def test_superstep_pending_writes_commit_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            left = store.task_create("Left", owner="implementer", task_id="left")
            right = store.task_create("Right", owner="tester", task_id="right")
            step = store.superstep_begin(
                ["implementer", "tester"],
                step_id="round-1",
                metadata={"purpose": "atomic round"},
            )
            self.assertEqual(step["status"], "open")
            store.task_claim(
                left["task_id"],
                "implementer",
                lease_seconds=60,
                superstep_id="round-1",
            )
            store.task_claim(
                right["task_id"],
                "tester",
                lease_seconds=60,
                superstep_id="round-1",
            )
            with self.assertRaises(RuntimeError):
                store.task_release(
                    left["task_id"],
                    "implementer",
                    status="done",
                    result={"forbidden": True},
                )

            staged_left = store.superstep_stage(
                "round-1",
                "implementer",
                [
                    {
                        "op": "task_release",
                        "task_id": "left",
                        "status": "done",
                        "summary": "left complete",
                        "result": {"value": 1},
                    },
                    {"op": "status_set", "key": "left_output", "value": 1},
                ],
            )
            self.assertFalse(staged_left["ready_to_commit"])
            tasks_before = {item["task_id"]: item for item in store.tasks()}
            self.assertEqual(tasks_before["left"]["status"], "active")
            self.assertEqual(tasks_before["left"]["claimed_by"], "implementer")
            self.assertNotIn("left_output", store.status())

            staged_right = store.superstep_stage(
                "round-1",
                "tester",
                [
                    {
                        "op": "task_release",
                        "task_id": "right",
                        "status": "done",
                        "summary": "right complete",
                        "result": {"value": 2},
                    },
                    {"op": "status_set", "key": "right_output", "value": 2},
                ],
            )
            self.assertTrue(staged_right["ready_to_commit"])
            tasks_ready = {item["task_id"]: item for item in store.tasks()}
            self.assertEqual(tasks_ready["left"]["status"], "active")
            self.assertEqual(tasks_ready["right"]["status"], "active")
            self.assertEqual(store.status(), {})

            committed = store.superstep_commit("round-1")
            self.assertEqual(committed["status"], "committed")
            self.assertEqual(committed["commit_summary"]["participant_count"], 2)
            self.assertEqual(committed["commit_summary"]["write_count"], 4)
            tasks_after = {item["task_id"]: item for item in store.tasks()}
            self.assertEqual(tasks_after["left"]["status"], "done")
            self.assertEqual(tasks_after["right"]["status"], "done")
            self.assertEqual(tasks_after["left"]["claimed_by"], "")
            self.assertEqual(tasks_after["right"]["claimed_by"], "")
            self.assertEqual(store.status()["left_output"]["value"], 1)
            self.assertEqual(store.status()["right_output"]["value"], 2)

    def test_superstep_failure_keeps_pending_writes_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            first = store.task_create("First", task_id="first")
            second = store.task_create("Second", task_id="second")
            store.superstep_begin(["worker-a", "worker-b"], step_id="round-fail")
            store.task_claim(
                first["task_id"],
                "worker-a",
                lease_seconds=60,
                superstep_id="round-fail",
            )
            store.task_claim(
                second["task_id"],
                "worker-b",
                lease_seconds=60,
                superstep_id="round-fail",
            )
            store.superstep_stage(
                "round-fail",
                "worker-a",
                [
                    {
                        "op": "task_release",
                        "task_id": "first",
                        "status": "done",
                        "result": "should-not-publish",
                    },
                    {"op": "status_set", "key": "partial", "value": True},
                ],
            )

            aborted = store.superstep_fail(
                "round-fail",
                "worker-b",
                "worker crashed",
            )
            self.assertEqual(aborted["status"], "aborted")
            tasks = {item["task_id"]: item for item in store.tasks()}
            self.assertEqual(tasks["first"]["status"], "active")
            self.assertEqual(tasks["first"]["result"], None)
            self.assertEqual(tasks["second"]["status"], "active")
            self.assertNotIn("partial", store.status())
            diagnostic = store.superstep_get("round-fail", include_pending=True)
            self.assertIn("worker-a", diagnostic["pending_writes"])
            with self.assertRaises(RuntimeError):
                store.superstep_commit("round-fail")

    def test_superstep_conflicting_writes_abort_without_partial_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            store.superstep_begin(["worker-a", "worker-b"], step_id="round-conflict")
            store.superstep_stage(
                "round-conflict",
                "worker-a",
                [{"op": "status_set", "key": "shared", "value": "a"}],
            )
            store.superstep_stage(
                "round-conflict",
                "worker-b",
                [{"op": "status_set", "key": "shared", "value": "b"}],
            )

            result = store.superstep_commit("round-conflict")
            self.assertEqual(result["status"], "conflict")
            self.assertIn("conflicting pending writes", result["error"])
            self.assertNotIn("shared", store.status())

if __name__ == "__main__":
    unittest.main()
