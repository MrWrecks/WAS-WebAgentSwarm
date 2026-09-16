import unittest

from web_agent_swarm.roles import plan_roles


class PlannerTest(unittest.TestCase):
    def test_requested_count(self):
        tasks = [
            {"status": "active", "owner": "implementer"},
            {"status": "review", "owner": "reviewer"},
        ]
        result = plan_roles(tasks, worker_count=2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["worker_count"], 2)
        self.assertEqual(set(result["roles"]), {"implementer", "reviewer"})

    def test_requested_roles(self):
        result = plan_roles([], requested_roles=["tester", "reviewer"])
        self.assertEqual(result["roles"], ["tester", "reviewer"])


if __name__ == "__main__":
    unittest.main()
