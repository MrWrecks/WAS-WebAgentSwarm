"""Process liveness probes must not kill live worker processes."""

import subprocess
import sys
import unittest

from web_agent_swarm.swarm import SwarmRuntime


class ProcessLivenessTests(unittest.TestCase):
    def test_probing_a_live_process_preserves_it(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertTrue(SwarmRuntime._pid_alive(child.pid))
            self.assertIsNone(child.poll(), "liveness probe terminated the process")
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        self.assertFalse(SwarmRuntime._pid_alive(child.pid))

    def test_invalid_pid_is_not_alive(self):
        self.assertFalse(SwarmRuntime._pid_alive(0))
        self.assertFalse(SwarmRuntime._pid_alive(-1))
        self.assertFalse(SwarmRuntime._pid_alive("invalid"))


if __name__ == "__main__":
    unittest.main()
