import tempfile
import unittest
from pathlib import Path

from web_agent_swarm.benchmark import (
    BENCHMARK_REVISION,
    benchmark_history,
    run_scheduler_benchmark,
)


class SchedulerBenchmarkTest(unittest.TestCase):
    def test_scheduler_benchmark_invariants(self):
        result = run_scheduler_benchmark()
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], BENCHMARK_REVISION)
        self.assertEqual(result["episode_count"], 7)
        self.assertEqual(result["passed_count"], 7)
        self.assertEqual(result["total_reward"], 7)
        self.assertTrue(all(item["passed"] for item in result["episodes"]))
        self.assertEqual(result["was_version"], "0.6.0")
        self.assertEqual(len(result["scheduler_source_hash"]), 64)

    def test_benchmark_journal_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = str(Path(tmp) / "journal.jsonl")
            result = run_scheduler_benchmark(
                record=True,
                journal_path=journal,
            )
            self.assertEqual(result["journal_path"], str(Path(journal).resolve()))
            history = benchmark_history(limit=1, journal_path=journal)
            self.assertTrue(history["ok"])
            self.assertEqual(len(history["runs"]), 1)
            self.assertEqual(
                history["runs"][0]["study_id"],
                result["study_id"],
            )
            self.assertEqual(history["runs"][0]["total_reward"], 7)


if __name__ == "__main__":
    unittest.main()
