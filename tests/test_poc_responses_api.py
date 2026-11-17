"""API PoC tests: local-only, no credentials or external requests."""
import tempfile
import unittest
from pathlib import Path

from scripts.poc_responses_api import ModelResponseError, run_workflow
from web_agent_swarm.state import DurableStore


class FakeClient:
    model = "synthetic-local-model"

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = 0

    def respond(self, instructions, text):
        self.calls += 1
        response = next(self.outputs)
        if isinstance(response, BaseException):
            raise response
        return response


class ModelProofTests(unittest.TestCase):
    def test_real_durable_store_manager_worker_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            client = FakeClient(["WORKER-1", "PROOF OF CONCEPT", "ACCEPT"])
            result = run_workflow(client, store)
            self.assertEqual(result["result"], "PASS")
            self.assertEqual(result["task_attempts"], 1)
            self.assertEqual(client.calls, 3)
            self.assertEqual(len(store.send_transactions()), 3)
            self.assertTrue(all(r["status"] == "ok"
                                for r in store.send_transactions()))
            self.assertEqual(store.tasks()[0]["status"], "done")

    def test_uncertain_model_transport_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            client = FakeClient([
                "WORKER-1",
                ModelResponseError("transport lost after submission"),
                "PROOF OF CONCEPT",
            ])
            with self.assertRaises(ModelResponseError):
                run_workflow(client, store)
            self.assertEqual(client.calls, 2)
            records = store.send_transactions()
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["status"], "ack_timeout")
            self.assertTrue(records[0]["may_have_executed"])
            self.assertEqual(store.tasks()[0]["status"], "active")
            self.assertEqual(store.tasks()[0]["attempts"], 1)

    def test_manager_rejects_incorrect_result_without_completing_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableStore(Path(tmp))
            client = FakeClient(["WORKER-1", "incorrect"])
            with self.assertRaises(ModelResponseError):
                run_workflow(client, store)
            self.assertEqual(client.calls, 2)
            self.assertEqual(store.tasks()[0]["status"], "active")


if __name__ == "__main__":
    unittest.main()
