import tempfile
import unittest
from pathlib import Path

from web_agent_swarm.state import DurableStore
from web_agent_swarm.swarm import SwarmRuntime


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

    def send(self, tab_id, text):
        self.sent.append((tab_id, text))
        for idx, tab in enumerate(self.tabs_data):
            if tab["id"] == tab_id and "/c/" not in tab["url"]:
                self.tabs_data[idx] = dict(
                    tab,
                    url="https://chatgpt.com/c/conversation-{0}".format(tab_id),
                )
        return {"ok": True, "tabId": tab_id}


class SwarmRuntimeTest(unittest.TestCase):
    def test_plan_does_not_touch_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            swarm = SwarmRuntime(bridge=bridge, store=DurableStore(Path(tmp)))
            plan = swarm.plan(worker_count=2)
            self.assertTrue(plan["requires_confirmation"])
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


if __name__ == "__main__":
    unittest.main()
