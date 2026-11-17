"""Cross-process durability lock regression tests."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from web_agent_swarm.state import DurableStore


class DurableStateLockTests(unittest.TestCase):
    def test_old_live_lock_is_not_stolen(self):
        with tempfile.TemporaryDirectory() as temp:
            owner = DurableStore(Path(temp))
            contender = DurableStore(Path(temp))
            with owner._write_lock():
                old = time.time() - 120
                os.utime(owner.lock_path, (old, old))
                with self.assertRaises(TimeoutError):
                    with contender._write_lock(timeout=0.1):
                        self.fail("a live lock was stolen")
            with contender._write_lock(timeout=0.2):
                pass

    def test_abandoned_lock_is_released_on_process_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            child = (
                "import os\n"
                "from pathlib import Path\n"
                "from web_agent_swarm.state import DurableStore\n"
                "store = DurableStore(Path(os.environ['WAS_TEST_LOCK_ROOT']))\n"
                "with store._write_lock():\n"
                "    os._exit(0)\n"
            )
            env = os.environ.copy()
            env["WAS_TEST_LOCK_ROOT"] = temp
            subprocess.run(
                [sys.executable, "-c", child],
                env=env, check=True, timeout=10,
                stdout=subprocess.DEVNULL,
            )
            with DurableStore(Path(temp))._write_lock(timeout=0.3):
                pass


if __name__ == "__main__":
    unittest.main()
