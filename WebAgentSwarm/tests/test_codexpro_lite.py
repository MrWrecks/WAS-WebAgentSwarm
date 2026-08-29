import tempfile
import unittest
from pathlib import Path

from web_agent_swarm.codexpro_core import ACTIONS, CodexProLiteRuntime


class CodexProLiteTest(unittest.TestCase):
    def make_runtime(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "README.md").write_text("# Demo\nhello world\n", encoding="utf-8")
        runtime = CodexProLiteRuntime(str(root))
        opened = runtime.open_current_workspace(include_tree=True)
        return tmp, root, runtime, opened["workspace_id"]

    def test_action_surface(self):
        tmp, _root, runtime, _wid = self.make_runtime()
        try:
            result = runtime.codexpro("list_actions")
            self.assertEqual(result["action_count"], len(ACTIONS))
            self.assertEqual(set(result["actions"]), set(ACTIONS))
        finally:
            tmp.cleanup()

    def test_workspace_read_search_write_edit(self):
        tmp, root, runtime, wid = self.make_runtime()
        try:
            read = runtime.read("README.md", workspace_id=wid)
            self.assertIn("hello world", read["text"])

            search = runtime.search("hello", workspace_id=wid)
            self.assertEqual(search["matches"][0]["path"], "README.md")

            runtime.write(
                "src/example.py",
                "value = 1\n",
                workspace_id=wid,
            )
            runtime.edit(
                "src/example.py",
                "value = 1",
                "value = 2",
                workspace_id=wid,
            )
            self.assertEqual(
                (root / "src" / "example.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )
        finally:
            tmp.cleanup()

    def test_path_escape_and_shell_chaining_are_blocked(self):
        tmp, _root, runtime, wid = self.make_runtime()
        try:
            with self.assertRaises(ValueError):
                runtime.read("../outside.txt", workspace_id=wid)
            with self.assertRaises(ValueError):
                runtime.bash("python --version && python --version", workspace_id=wid)
        finally:
            tmp.cleanup()

    def test_context_and_handoff(self):
        tmp, root, runtime, wid = self.make_runtime()
        try:
            compact = runtime.compact_context(
                workspace_id=wid,
                summary="current state",
                decisions=["keep scope small"],
                next_steps=["run tests"],
            )
            self.assertTrue(compact["ok"])
            resumed = runtime.resume_context(workspace_id=wid)
            self.assertIn("current state", resumed["text"])

            handoff = runtime.handoff_to_agent(
                workspace_id=wid,
                agent="custom",
                plan="Implement the next change.",
            )
            self.assertTrue(handoff["ok"])
            self.assertTrue((root / ".ai-bridge" / "current-plan.md").is_file())
        finally:
            tmp.cleanup()

    def test_allowlisted_verification_command(self):
        tmp, _root, runtime, wid = self.make_runtime()
        try:
            result = runtime.bash("python --version", workspace_id=wid)
            self.assertEqual(result["exitCode"], 0)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
