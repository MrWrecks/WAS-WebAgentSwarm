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


if __name__ == "__main__":
    unittest.main()
