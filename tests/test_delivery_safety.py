"""No automatic retry when a browser send may already have executed."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

from web_agent_swarm.bridge import BridgeClient
from web_agent_swarm.state import DurableStore
from web_agent_swarm.swarm import SwarmRuntime


class UncertainBridge:
    def __init__(self):
        self.sent = []

    def inspect(self, tab_id, tail=1):
        return {"ok": True, "snapshot": {"generating": False}}

    def send(self, tab_id, message, command_id=""):
        self.sent.append((tab_id, message, command_id))
        return {"ok": False, "mayHaveExecuted": True, "error": "response lost"}


class DeliverySafetyTests(unittest.TestCase):
    def test_coordinator_does_not_retry_ambiguous_send(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = UncertainBridge()
            runtime = SwarmRuntime(bridge=bridge, store=DurableStore(Path(temp)))
            result = runtime._send_when_ready(7, "One request", timeout=1)
            self.assertFalse(result["ok"])
            self.assertTrue(result["uncertain"])
            self.assertEqual(1, len(bridge.sent))

    def test_transport_failure_is_reported_as_uncertain(self):
        client = BridgeClient("http://example.invalid", token="example")
        with mock.patch("web_agent_swarm.bridge.request.urlopen",
                        side_effect=error.URLError("connection closed")):
            result = client.send(7, "One request", command_id="fixed-123")
        self.assertFalse(result["ok"])
        self.assertTrue(result["mayHaveExecuted"])
        self.assertEqual("fixed-123", result["command_id"])

    def test_direct_send_gets_a_trackable_command_id(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(client, "_json", return_value={"ok": True}) as call:
            result = client.send(7, "One request")
        submitted = call.call_args.kwargs["payload"]
        self.assertTrue(submitted["commandId"])
        self.assertEqual(submitted["commandId"], result["command_id"])

    def test_bound_send_passes_expected_url(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = SwarmRuntime(bridge=UncertainBridge(), store=DurableStore(Path(temp)))
            runtime.store.bind("main", {"kind": "main", "conversation_id": "synthetic"})
            with mock.patch.object(runtime, "_resolve", return_value={
                "id": 7, "url": "https://chatgpt.com/c/synthetic"
            }), mock.patch.object(runtime, "_send_when_ready", return_value={
                "ok": False, "mayHaveExecuted": True, "error": "response lost"
            }) as send:
                result = runtime.send("main", "One request")
            self.assertEqual(
                send.call_args.kwargs["expected_url"],
                "https://chatgpt.com/c/synthetic"
            )
            self.assertTrue(result["send_health"]["may_have_executed"])
            self.assertEqual(result["send_transaction"]["status"], "ack_timeout")

    def test_client_forwards_expected_url(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(client, "_json", return_value={"ok": True}) as call:
            client.send(7, "One request", expected_url="https://chatgpt.com/c/synthetic")
        self.assertEqual(
            call.call_args.kwargs["payload"]["expectedUrl"],
            "https://chatgpt.com/c/synthetic"
        )

    def test_tab_close_requires_matching_url_when_supplied(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(client, "_json", return_value={"ok": True}) as call:
            client.close_tab(7, expected_url="https://chatgpt.com/c/synthetic")
        self.assertEqual(call.call_args.kwargs["payload"], {
            "tabId": 7, "expectedUrl": "https://chatgpt.com/c/synthetic"
        })

    def test_transport_loss_on_open_and_close_is_uncertain(self):
        client = BridgeClient("http://example.invalid", token="example")
        with mock.patch("web_agent_swarm.bridge.request.urlopen",
                        side_effect=error.URLError("response lost")):
            self.assertTrue(client.open_tab()["mayHaveExecuted"])
            self.assertTrue(client.close_tab(7)["mayHaveExecuted"])

    def test_extension_persists_intent_before_sending(self):
        source = (Path(__file__).resolve().parents[1] /
                  "browser-extension" / "background.js").read_text(encoding="utf-8")
        self.assertLess(
            source.index("await rememberCommand(commandId, {"),
            source.index("const result = { ...await performSend(tab, payload), fingerprint }")
        )
        self.assertIn("persistQueue = persistQueue.catch(() => {}).then(", source)


if __name__ == "__main__":
    unittest.main()
