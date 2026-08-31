import unittest
from unittest import mock

from web_agent_swarm.bridge import BridgeClient


class BridgeClientTest(unittest.TestCase):
    def test_open_tab_uses_connected_browser_profile(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(
            client,
            "_json",
            return_value={"ok": True, "tab": {"id": 1}},
        ) as bridge_call:
            result = client.open_tab("https://chatgpt.com/")
        self.assertTrue(result["ok"])
        self.assertEqual(result["tab"]["id"], 1)
        bridge_call.assert_called_once_with(
            "/open",
            method="POST",
            payload={"url": "https://chatgpt.com/", "active": False},
            timeout=12.0,
        )

    def test_browser_events_support_cursor_and_long_poll(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(
            client,
            "_json",
            return_value={"ok": True, "events": [], "latestSeq": 7},
        ) as bridge_call:
            result = client.events(since=3, limit=9, wait_ms=1500)
        self.assertTrue(result["ok"])
        bridge_call.assert_called_once_with(
            "/events?since=3&limit=9&waitMs=1500",
            timeout=4.0,
        )

    def test_send_carries_command_id_for_idempotent_retry(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(
            client,
            "_json",
            return_value={"ok": True},
        ) as bridge_call:
            result = client.send(7, "continue", command_id="cmd-123")
        self.assertTrue(result["ok"])
        bridge_call.assert_called_once_with(
            "/send",
            method="POST",
            payload={"tabId": 7, "text": "continue", "commandId": "cmd-123"},
            timeout=14.0,
        )

    def test_close_tab_uses_bridge_close_endpoint(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(
            client,
            "_json",
            return_value={"ok": True, "tabId": 7},
        ) as bridge_call:
            result = client.close_tab(7)
        self.assertTrue(result["ok"])
        bridge_call.assert_called_once_with(
            "/close",
            method="POST",
            payload={"tabId": 7},
            timeout=9.0,
        )

    def test_open_tab_never_launches_another_profile(self):
        client = BridgeClient("http://example.invalid")
        with mock.patch.object(
            client,
            "_json",
            return_value={
                "ok": False,
                "http_status": 504,
                "error": "Timed out waiting for extension response.",
            },
        ):
            result = client.open_tab("https://chatgpt.com/")
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["policy"],
            "current_connected_browser_profile_only",
        )
        self.assertIn("will not launch another Edge/Chrome profile", result["recovery"])

    def test_bridge_token_is_lazily_reloaded(self):
        client = BridgeClient("http://example.invalid", token="")
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with mock.patch("web_agent_swarm.bridge.load_bridge_token", return_value="x" * 64), mock.patch("web_agent_swarm.bridge.request.urlopen", return_value=response) as opened:
            result = client._json("/health")
        self.assertTrue(result["ok"])
        self.assertEqual(client.token, "x" * 64)
        request_object = opened.call_args.args[0]
        self.assertEqual(request_object.get_header("X-was-token"), "x" * 64)


if __name__ == "__main__":
    unittest.main()
