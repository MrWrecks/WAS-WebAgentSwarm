"""Static checks for the extension's permission and reconnection safeguards."""

import json
import unittest
from pathlib import Path


EXTENSION = Path(__file__).resolve().parents[1] / "browser-extension"


class ExtensionSafetyTests(unittest.TestCase):
    def test_manifest_permissions_are_scoped(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("tabs", manifest["permissions"])
        self.assertEqual(["https://chatgpt.com/*"], manifest["content_scripts"][0]["matches"])
        self.assertTrue(all(
            pattern.startswith(("http://", "https://"))
            for pattern in manifest["host_permissions"]
        ))

    def test_reconnect_alarm_is_declared(self):
        source = (EXTENSION / "background.js").read_text(encoding="utf-8")
        self.assertIn("const RECONNECT_ALARM = 'wasBridgeReconnect';", source)
        self.assertIn("chrome.alarms.create(RECONNECT_ALARM", source)

    def test_only_chatgpt_tabs_are_returned(self):
        source = (EXTENSION / "background.js").read_text(encoding="utf-8")
        handler = source.split("if (payload.type === 'list-tabs') {", 1)[1].split(
            "return;", 1
        )[0]
        self.assertEqual(
            2, handler.count("chrome.tabs.query({ url: 'https://chatgpt.com/*' })")
        )

    def test_unrelated_chatgpt_tabs_not_auto_protected(self):
        source = (EXTENSION / "background.js").read_text(encoding="utf-8")
        self.assertIn("protectChatTab(tabOrId, shouldProtect = false)", source)
        self.assertIn("if (shouldProtect && tab.autoDiscardable !== false)", source)
        self.assertIn("protectChatTab(tab, true)", source)


if __name__ == "__main__":
    unittest.main()
