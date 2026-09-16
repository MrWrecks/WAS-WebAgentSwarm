import tempfile
import time
import unittest
from pathlib import Path

from web_agent_swarm.mvcc import LaneManager, normalize_resource, resources_overlap
from web_agent_swarm.state import DurableStore


class MVCCGuardHarness:
    """Small test-only file/tool harness around WAS LaneManager guards.

    The authoritative CodexPro MCP is the vendored Node/TypeScript implementation.
    These tests exercise WAS MVCC semantics directly rather than depending on a
    second Python MCP implementation.
    """

    def __init__(self, root, state_root):
        self.root = Path(root).resolve()
        self.store = DurableStore(Path(state_root))
        self.lanes = LaneManager(self.store)
        self.workspace_id = "test-workspace"

    def open_current_workspace(self):
        return {"workspace_id": self.workspace_id, "root": str(self.root)}

    def _target(self, relative):
        target = (self.root / relative).resolve()
        target.relative_to(self.root)
        return target

    def read(self, path, workspace_id=""):
        self.lanes.guard_read(self.root, path)
        target = self._target(path)
        return {"text": target.read_text(encoding="utf-8")}

    def write(self, path, content, workspace_id=""):
        self.lanes.guard_mutation(self.root, path, operation="write")
        target = self._target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": path}

    def edit(self, path, old_text, new_text, workspace_id=""):
        self.lanes.guard_read(self.root, path)
        self.lanes.guard_mutation(self.root, path, operation="edit")
        target = self._target(path)
        text = target.read_text(encoding="utf-8")
        if old_text not in text:
            raise ValueError("old_text not found")
        target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return {"path": path, "replacements": 1}

    def search(self, query, workspace_id=""):
        self.lanes.record_opaque_read(self.root, command="search")
        matches = []
        for item in self.root.rglob("*"):
            if not item.is_file():
                continue
            try:
                body = item.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if query in body:
                matches.append(str(item.relative_to(self.root)))
        return {"matches": matches}

    def bash(self, command, workspace_id=""):
        self.lanes.guard_mutation(self.root, "", operation="bash")
        return {"command": command, "exitCode": 0}


class MVCCLaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical = root / "canonical"
        self.state_root = root / "state"
        self.canonical.mkdir()
        (self.canonical / "src").mkdir()
        (self.canonical / "docs").mkdir()
        (self.canonical / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")
        (self.canonical / "src" / "b.py").write_text("other = 1\n", encoding="utf-8")
        (self.canonical / "docs" / "note.md").write_text("base\n", encoding="utf-8")
        self.store = DurableStore(self.state_root)
        self.lanes = LaneManager(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def _task(self, task_id, worker):
        self.store.task_create(task_id, owner=worker, task_id=task_id)
        return self.store.task_claim(task_id, worker, lease_seconds=120)

    def _runtime(self, root):
        runtime = MVCCGuardHarness(str(root), state_root=str(self.state_root))
        opened = runtime.open_current_workspace()
        return runtime, opened["workspace_id"]

    def test_resource_hierarchy_overlap(self):
        parent = normalize_resource("src/autol")
        child = normalize_resource("src/autol/web_agent_swarm.py")
        peer = normalize_resource("src/other.py")
        logical_a = normalize_resource("experiment:drc/17net")
        logical_b = normalize_resource("experiment:drc")
        self.assertTrue(resources_overlap(parent, child))
        self.assertFalse(resources_overlap(parent, peer))
        self.assertTrue(resources_overlap(logical_a, logical_b))
        self.assertFalse(resources_overlap(parent, logical_a))

    def test_hierarchical_claim_conflict_and_disjoint_parallel_lane(self):
        self._task("t1", "implementer")
        self._task("t2", "tester")
        first = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src"],
            lease_seconds=120,
        )
        with self.assertRaises(RuntimeError):
            self.lanes.start(
                "t2",
                "tester",
                str(self.canonical),
                ["src/a.py"],
                lease_seconds=120,
            )

        self.store.task_create("t3", owner="reviewer", task_id="t3")
        self.store.task_claim("t3", "reviewer", lease_seconds=120)
        second = self.lanes.start(
            "t3",
            "reviewer",
            str(self.canonical),
            ["docs"],
            lease_seconds=120,
        )
        self.assertNotEqual(
            first["lane"]["workspace_path"],
            second["lane"]["workspace_path"],
        )

    def test_canonical_mutation_is_hard_blocked_and_lane_write_commits(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
            lease_seconds=120,
        )
        lane = started["lane"]

        canonical_runtime, canonical_wid = self._runtime(self.canonical)
        with self.assertRaises(RuntimeError):
            canonical_runtime.write(
                "src/a.py",
                "value = 99\n",
                workspace_id=canonical_wid,
            )
        with self.assertRaises(RuntimeError):
            canonical_runtime.bash("python --version", workspace_id=canonical_wid)

        lane_runtime, lane_wid = self._runtime(Path(lane["workspace_path"]))
        lane_runtime.edit(
            "src/a.py",
            "value = 1",
            "value = 2",
            workspace_id=lane_wid,
        )
        self.assertEqual(
            (self.canonical / "src" / "a.py").read_text(encoding="utf-8"),
            "value = 1\n",
        )
        with self.assertRaises(RuntimeError):
            self.store.task_release("t1", "implementer", status="done")

        committed = self.lanes.commit(lane["lane_id"])
        self.assertTrue(committed["ok"])
        self.assertEqual(committed["write_paths"], ["src/a.py"])
        self.assertEqual(
            (self.canonical / "src" / "a.py").read_text(encoding="utf-8"),
            "value = 2\n",
        )
        self.assertEqual(committed["canonical_epoch_after"], 1)
        durable = self.store.load()
        version_key = started["run"]["canonical_id"] + "::src/a.py"
        version = durable["resource_versions"][version_key]
        self.assertEqual(version["version"], 1)
        self.assertEqual(version["changed_by_run"], started["run"]["run_id"])
        self.assertEqual(
            durable["canonical_epochs"][started["run"]["canonical_id"]],
            1,
        )

        released = self.store.task_release("t1", "implementer", status="done")
        self.assertEqual(released["status"], "done")

    def test_lane_write_outside_hierarchical_claim_is_blocked(self):
        self._task("t1", "implementer")
        lane = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
        )["lane"]
        runtime, wid = self._runtime(Path(lane["workspace_path"]))
        with self.assertRaises(RuntimeError):
            runtime.write("docs/note.md", "bad\n", workspace_id=wid)
        self.assertEqual(
            (Path(lane["workspace_path"]) / "docs" / "note.md").read_text(
                encoding="utf-8"
            ),
            "base\n",
        )

    def test_write_conflict_never_overwrites_canonical(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
        )
        lane_root = Path(started["lane"]["workspace_path"])
        runtime, wid = self._runtime(lane_root)
        runtime.edit(
            "src/a.py",
            "value = 1",
            "value = 2",
            workspace_id=wid,
        )

        # Simulate a rogue external actor outside the guarded bundled CodexPro path.
        (self.canonical / "src" / "a.py").write_text(
            "value = 77\n", encoding="utf-8"
        )
        result = self.lanes.commit(started["lane"]["lane_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicts"][0]["path"], "src/a.py")
        self.assertEqual(result["conflicts"][0]["kind"], "write")
        self.assertEqual(
            (self.canonical / "src" / "a.py").read_text(encoding="utf-8"),
            "value = 77\n",
        )

    def test_precise_read_set_detects_stale_dependency(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
        )
        runtime, wid = self._runtime(Path(started["lane"]["workspace_path"]))
        read = runtime.read("src/b.py", workspace_id=wid)
        self.assertIn("other = 1", read["text"])
        runtime.edit(
            "src/a.py",
            "value = 1",
            "value = 2",
            workspace_id=wid,
        )

        (self.canonical / "src" / "b.py").write_text(
            "other = 2\n", encoding="utf-8"
        )
        result = self.lanes.commit(started["lane"]["lane_id"])
        self.assertFalse(result["ok"])
        conflicts = {item["path"]: item for item in result["conflicts"]}
        self.assertEqual(conflicts["src/b.py"]["kind"], "read")
        self.assertEqual(
            (self.canonical / "src" / "a.py").read_text(encoding="utf-8"),
            "value = 1\n",
        )

    def test_search_or_command_marks_opaque_read_and_serializes_commit(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
        )
        runtime, wid = self._runtime(Path(started["lane"]["workspace_path"]))
        runtime.search("other", workspace_id=wid)
        runtime.edit(
            "src/a.py",
            "value = 1",
            "value = 2",
            workspace_id=wid,
        )
        lane_state = self.lanes.get(started["lane"]["lane_id"])["lane"]
        self.assertTrue(lane_state["opaque_reads"])

        (self.canonical / "docs" / "note.md").write_text(
            "changed elsewhere\n", encoding="utf-8"
        )
        result = self.lanes.commit(started["lane"]["lane_id"])
        self.assertFalse(result["ok"])
        self.assertIn(
            "docs/note.md",
            {item["path"] for item in result["conflicts"]},
        )

    def test_conflict_can_fork_from_latest_canonical(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
        )
        runtime, wid = self._runtime(Path(started["lane"]["workspace_path"]))
        runtime.edit(
            "src/a.py",
            "value = 1",
            "value = 2",
            workspace_id=wid,
        )
        (self.canonical / "src" / "a.py").write_text(
            "value = 9\n", encoding="utf-8"
        )
        conflict = self.lanes.commit(started["lane"]["lane_id"])
        self.assertEqual(conflict["status"], "conflict")

        forked = self.lanes.fork(started["lane"]["lane_id"])
        self.assertEqual(
            forked["run"]["parent_run_id"],
            started["run"]["run_id"],
        )
        self.assertEqual(
            (Path(forked["lane"]["workspace_path"]) / "src" / "a.py").read_text(
                encoding="utf-8"
            ),
            "value = 9\n",
        )

    def test_abort_releases_lane_before_task_release(self):
        self._task("t1", "implementer")
        lane = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
        )["lane"]
        with self.assertRaises(RuntimeError):
            self.store.task_release(
                "t1",
                "implementer",
                status="failed",
                retryable=False,
            )
        aborted = self.lanes.abort(lane["lane_id"], reason="test failure")
        self.assertEqual(aborted["status"], "aborted")
        released = self.store.task_release(
            "t1",
            "implementer",
            status="failed",
            retryable=False,
        )
        self.assertEqual(released["status"], "failed")

    def test_task_heartbeat_renews_lane_claims(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
            lease_seconds=10,
        )
        run_id = started["run"]["run_id"]
        before = self.lanes.claims(status="active")[0]["lease_expires_at"]
        time.sleep(0.01)
        self.store.task_heartbeat("t1", "implementer", lease_seconds=120)
        active = [
            claim
            for claim in self.store.load()["resource_claims"].values()
            if claim["run_id"] == run_id and claim["status"] == "active"
        ]
        self.assertGreater(active[0]["lease_expires_at"], before)

    def test_resource_claims_are_scoped_by_canonical_workspace(self):
        self._task("t1", "implementer")
        self._task("t2", "tester")
        other = Path(self.tmp.name) / "canonical-two"
        (other / "src").mkdir(parents=True)
        (other / "src" / "a.py").write_text("value = 5\n", encoding="utf-8")

        first = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src"],
        )
        second = self.lanes.start(
            "t2",
            "tester",
            str(other),
            ["src"],
        )
        self.assertNotEqual(
            first["run"]["canonical_id"],
            second["run"]["canonical_id"],
        )
        self.assertEqual(len(self.lanes.claims(status="active")), 2)

    def test_scratch_claim_is_lane_local_and_not_published(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            [
                {"resource": "src/a.py", "mode": "write"},
                {"resource": "scratch", "mode": "scratch"},
            ],
        )
        runtime, wid = self._runtime(Path(started["lane"]["workspace_path"]))
        runtime.edit(
            "src/a.py",
            "value = 1",
            "value = 2",
            workspace_id=wid,
        )
        runtime.write(
            "scratch/result.txt",
            "temporary experiment output\n",
            workspace_id=wid,
        )
        committed = self.lanes.commit(started["lane"]["lane_id"])
        self.assertEqual(committed["write_paths"], ["src/a.py"])
        self.assertEqual(committed["scratch_paths"], ["scratch/result.txt"])
        self.assertFalse((self.canonical / "scratch" / "result.txt").exists())
        self.assertEqual(
            (self.canonical / "src" / "a.py").read_text(encoding="utf-8"),
            "value = 2\n",
        )

    def test_restart_recovery_rolls_back_partial_filesystem_commit(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src"],
            lease_seconds=120,
        )
        lane = started["lane"]
        run = started["run"]
        commit_id = "commit_crash_test"
        backup_root = Path(lane["lane_root"]) / "commit-backup" / commit_id
        backup = backup_root / "src" / "a.py"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text("value = 1\n", encoding="utf-8")
        journal_path = backup_root / "journal.json"
        journal = {
            "commit_id": commit_id,
            "run_id": run["run_id"],
            "lane_id": lane["lane_id"],
            "canonical_id": run["canonical_id"],
            "canonical_root": str(self.canonical),
            "status": "prepared",
            "write_paths": ["src/a.py", "src/new.py"],
            "scratch_paths": [],
            "backups": {
                "src/a.py": {"type": "file", "path": str(backup)},
            },
            "created_paths": ["src/new.py"],
        }
        LaneManager._write_json_atomic(journal_path, journal)

        (self.canonical / "src" / "a.py").write_text("value = 99\n", encoding="utf-8")
        (self.canonical / "src" / "new.py").write_text("partial\n", encoding="utf-8")
        data = self.store.load()
        data["lanes"][lane["lane_id"]]["status"] = "committing"
        data["runs"][run["run_id"]]["status"] = "committing"
        data["commits"][commit_id] = {
            "commit_id": commit_id,
            "run_id": run["run_id"],
            "lane_id": lane["lane_id"],
            "canonical_id": run["canonical_id"],
            "status": "committing",
            "journal_path": str(journal_path),
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.store.save(data)

        recovered_manager = LaneManager(self.store)
        recovered = recovered_manager.commits()
        record = next(item for item in recovered if item["commit_id"] == commit_id)
        self.assertEqual(record["status"], "recovered_rollback")
        self.assertEqual(
            (self.canonical / "src" / "a.py").read_text(encoding="utf-8"),
            "value = 1\n",
        )
        self.assertFalse((self.canonical / "src" / "new.py").exists())
        self.assertEqual(
            recovered_manager.get(lane["lane_id"])["lane"]["status"],
            "active",
        )

    def test_expired_claim_no_longer_protects_canonical_and_lane_cannot_commit(self):
        self._task("t1", "implementer")
        started = self.lanes.start(
            "t1",
            "implementer",
            str(self.canonical),
            ["src/a.py"],
        )
        data = self.store.load()
        for claim in data["resource_claims"].values():
            if claim["run_id"] == started["run"]["run_id"]:
                claim["lease_expires_at"] = 0
        self.store.save(data)

        canonical_runtime, canonical_wid = self._runtime(self.canonical)
        canonical_runtime.write(
            "src/a.py",
            "value = 3\n",
            workspace_id=canonical_wid,
        )
        with self.assertRaises(RuntimeError):
            self.lanes.commit(started["lane"]["lane_id"])


if __name__ == "__main__":
    unittest.main()
