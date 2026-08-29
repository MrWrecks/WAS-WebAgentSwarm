"""Small durable coordination store. No chat transcript is persisted by default."""

import copy
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional


def default_state_dir() -> Path:
    configured = os.environ.get("WEB_SWARM_STATE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".web-agent-swarm").resolve()


class DurableStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (root or default_state_dir()).resolve()
        self.path = self.root / "state.json"
        self.lock_path = self.root / "state.lock"

    @contextmanager
    def _write_lock(self, timeout: float = 5.0):
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + max(0.1, float(timeout))
        fd = None
        while fd is None:
            try:
                fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > 30.0:
                        self.lock_path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    raise TimeoutError("timed out waiting for durable store write lock")
                time.sleep(0.02)
        try:
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            yield
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                self.lock_path.unlink()
            except OSError:
                pass

    def _empty(self) -> Dict[str, object]:
        return {
            "schema_version": 4,
            "bindings": {},
            "tasks": {},
            "shared_status": {},
            "supersteps": {},
        }

    def load(self) -> Dict[str, object]:
        if not self.path.is_file():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        base = self._empty()
        for key in base:
            if key == "schema_version":
                continue
            if isinstance(data.get(key), type(base[key])):
                base[key] = data[key]
        for task in base["tasks"].values():
            if isinstance(task, dict):
                deps = task.get("dependencies")
                task["dependencies"] = [
                    str(item) for item in (deps if isinstance(deps, list) else [])
                ]
                task.setdefault("payload", None)
                task.setdefault("group_id", "")
                task.setdefault("group_index", -1)
                task.setdefault("parent_task_id", "")
                task.setdefault("result", None)
                task.setdefault("superstep_id", "")
                task.setdefault("claimed_by", "")
                task.setdefault("lease_expires_at", 0.0)
                task.setdefault("attempts", 0)
                task.setdefault("max_attempts", 3)
                task.setdefault("last_error", "")
                task.setdefault("retry_after", 0.0)
        return base

    def save(self, data: Dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp), str(self.path))

    def bind(self, name: str, binding: Dict[str, object]) -> Dict[str, object]:
        with self._write_lock():
            data = self.load()
            data["bindings"][str(name)] = dict(binding)
            self.save(data)
            return data["bindings"][str(name)]

    def unbind(self, name: str) -> bool:
        with self._write_lock():
            data = self.load()
            removed = data["bindings"].pop(str(name), None) is not None
            self.save(data)
            return removed

    def bindings(self) -> Dict[str, object]:
        return dict(self.load()["bindings"])

    def task_create(
        self,
        title: str,
        description: str = "",
        owner: str = "",
        task_id: str = "",
        dependencies: Optional[List[str]] = None,
        payload: object = None,
        group_id: str = "",
        group_index: int = -1,
        parent_task_id: str = "",
        max_attempts: int = 3,
    ) -> Dict[str, object]:
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("title is required")
        with self._write_lock():
            data = self.load()
            active_id = str(task_id or "").strip() or "task_" + uuid.uuid4().hex[:10]
            if active_id in data["tasks"]:
                raise ValueError("task_id already exists")
            deps = []
            for item in dependencies or []:
                dep = str(item or "").strip()
                if dep and dep not in deps:
                    deps.append(dep)
            missing = [dep for dep in deps if dep not in data["tasks"]]
            if missing:
                raise ValueError("unknown dependency task(s): {0}".format(", ".join(missing)))
            if active_id in deps:
                raise ValueError("task cannot depend on itself")
            clean_parent = str(parent_task_id or "").strip()
            if clean_parent and clean_parent not in data["tasks"]:
                raise ValueError("unknown parent_task_id")
            attempts_limit = max(1, int(max_attempts or 1))
            task = {
                "task_id": active_id,
                "title": clean_title,
                "description": str(description or "").strip(),
                "owner": str(owner or "").strip(),
                "dependencies": deps,
                "payload": payload,
                "group_id": str(group_id or "").strip(),
                "group_index": int(group_index),
                "parent_task_id": clean_parent,
                "status": "ready",
                "summary": "",
                "result": None,
                "superstep_id": "",
                "claimed_by": "",
                "lease_expires_at": 0.0,
                "attempts": 0,
                "max_attempts": attempts_limit,
                "last_error": "",
                "retry_after": 0.0,
                "updated_at": time.time(),
            }
            data["tasks"][active_id] = task
            self.save(data)
            return dict(task)

    def task_claim(
        self,
        task_id: str,
        worker_name: str,
        lease_seconds: int = 900,
        superstep_id: str = "",
    ) -> Dict[str, object]:
        clean_worker = str(worker_name or "").strip()
        clean_step = str(superstep_id or "").strip()
        if not clean_worker:
            raise ValueError("worker_name is required")
        with self._write_lock():
            data = self.load()
            if clean_step:
                step = data["supersteps"].get(clean_step)
                if not isinstance(step, dict):
                    raise KeyError("unknown superstep_id")
                if step.get("status") not in {"open", "ready"}:
                    raise RuntimeError("superstep is not open")
                if clean_worker not in step.get("participants", []):
                    raise RuntimeError("worker is not a superstep participant")
            task = data["tasks"].get(str(task_id))
            if not isinstance(task, dict):
                raise KeyError("unknown task_id")
            now = time.time()
            unresolved = [
                dep
                for dep in task.get("dependencies", [])
                if data["tasks"].get(str(dep), {}).get("status") != "done"
            ]
            if unresolved:
                raise RuntimeError(
                    "task dependencies are not done: {0}".format(", ".join(unresolved))
                )
            if float(task.get("retry_after") or 0.0) > now:
                raise RuntimeError("task retry delay has not elapsed")
            status = str(task.get("status") or "")
            if status not in {"ready", "active"}:
                raise RuntimeError("task is not claimable in status {0}".format(status))
            current_worker = str(task.get("claimed_by") or "")
            lease_expires = float(task.get("lease_expires_at") or 0.0)
            if current_worker and current_worker != clean_worker and lease_expires > now:
                raise RuntimeError("task is already leased by {0}".format(current_worker))
            attempts = int(task.get("attempts") or 0)
            max_attempts = max(1, int(task.get("max_attempts") or 1))
            if current_worker != clean_worker:
                if attempts >= max_attempts:
                    task["status"] = "failed"
                    task["last_error"] = "max attempts exhausted before claim"
                    task["updated_at"] = now
                    self.save(data)
                    raise RuntimeError("task max attempts exhausted")
                attempts += 1
            existing_step = str(task.get("superstep_id") or "")
            if existing_step and existing_step != clean_step:
                existing = data["supersteps"].get(existing_step)
                if isinstance(existing, dict) and existing.get("status") in {"open", "ready"}:
                    raise RuntimeError("task is already bound to active superstep {0}".format(existing_step))
            task["attempts"] = attempts
            task["status"] = "active"
            task["superstep_id"] = clean_step
            task["claimed_by"] = clean_worker
            task["lease_expires_at"] = now + max(1, int(lease_seconds or 1))
            task["updated_at"] = now
            self.save(data)
            return dict(task)

    def task_heartbeat(
        self,
        task_id: str,
        worker_name: str,
        lease_seconds: int = 900,
    ) -> Dict[str, object]:
        clean_worker = str(worker_name or "").strip()
        with self._write_lock():
            data = self.load()
            task = data["tasks"].get(str(task_id))
            if not isinstance(task, dict):
                raise KeyError("unknown task_id")
            if str(task.get("claimed_by") or "") != clean_worker:
                raise RuntimeError("task is not leased by this worker")
            if str(task.get("status") or "") != "active":
                raise RuntimeError("task is not active")
            now = time.time()
            task["lease_expires_at"] = now + max(1, int(lease_seconds or 1))
            task["updated_at"] = now
            self.save(data)
            return dict(task)

    def task_release(
        self,
        task_id: str,
        worker_name: str,
        status: str = "done",
        summary: str = "",
        result: object = None,
        error: str = "",
        retryable: bool = True,
        retry_delay_seconds: float = 0.0,
    ) -> Dict[str, object]:
        clean_worker = str(worker_name or "").strip()
        clean_status = str(status or "").strip().lower()
        if clean_status not in {"ready", "blocked", "review", "done", "failed"}:
            raise ValueError("invalid release status")
        with self._write_lock():
            data = self.load()
            task = data["tasks"].get(str(task_id))
            if not isinstance(task, dict):
                raise KeyError("unknown task_id")
            if str(task.get("claimed_by") or "") != clean_worker:
                raise RuntimeError("task is not leased by this worker")
            active_step_id = str(task.get("superstep_id") or "")
            if active_step_id:
                active_step = data["supersteps"].get(active_step_id)
                if isinstance(active_step, dict) and active_step.get("status") in {"open", "ready"}:
                    raise RuntimeError(
                        "task belongs to active superstep {0}; stage pending writes instead".format(
                            active_step_id
                        )
                    )
            now = time.time()
            attempts = int(task.get("attempts") or 0)
            max_attempts = max(1, int(task.get("max_attempts") or 1))
            final_status = clean_status
            last_error = str(error or "").strip()
            retry_after = 0.0
            if clean_status == "failed" and retryable and attempts < max_attempts:
                final_status = "ready"
                retry_after = now + max(0.0, float(retry_delay_seconds or 0.0))
            task["status"] = final_status
            task["summary"] = str(summary or "").strip()
            if result is not None:
                task["result"] = result
            task["last_error"] = last_error
            task["retry_after"] = retry_after
            task["superstep_id"] = ""
            task["claimed_by"] = ""
            task["lease_expires_at"] = 0.0
            task["updated_at"] = now
            self.save(data)
            return dict(task)

    def task_fanout(
        self,
        items: List[object],
        owner: str,
        title_prefix: str = "Shard",
        dependencies: Optional[List[str]] = None,
        group_id: str = "",
        max_attempts: int = 3,
    ) -> Dict[str, object]:
        values = list(items or [])
        if not values:
            raise ValueError("items must be non-empty")
        clean_group = str(group_id or "").strip() or "group_" + uuid.uuid4().hex[:10]
        created = []
        for index, item in enumerate(values, start=1):
            created.append(
                self.task_create(
                    title="{0} {1}".format(str(title_prefix or "Shard").strip(), index),
                    owner=owner,
                    dependencies=dependencies,
                    payload=item,
                    group_id=clean_group,
                    group_index=index - 1,
                    max_attempts=max_attempts,
                )
            )
        return {
            "group_id": clean_group,
            "task_ids": [item["task_id"] for item in created],
            "tasks": created,
        }

    def task_reduce(self, group_id: str) -> Dict[str, object]:
        clean_group = str(group_id or "").strip()
        if not clean_group:
            raise ValueError("group_id is required")
        members = [
            task
            for task in self.tasks()
            if str(task.get("group_id") or "") == clean_group
        ]
        members.sort(
            key=lambda item: (
                int(item.get("group_index", -1)),
                str(item.get("task_id") or ""),
            )
        )
        if not members:
            raise KeyError("unknown group_id")
        done = [task for task in members if task.get("status") == "done"]
        failed = [task for task in members if task.get("status") == "failed"]
        return {
            "group_id": clean_group,
            "complete": len(done) == len(members),
            "failed": bool(failed),
            "member_count": len(members),
            "done_count": len(done),
            "failed_task_ids": [task["task_id"] for task in failed],
            "results": [task.get("result") for task in done],
            "tasks": members,
        }

    @staticmethod
    def _normalize_superstep_writes(writes: List[Dict[str, object]]) -> List[Dict[str, object]]:
        normalized = []
        for raw in writes or []:
            if not isinstance(raw, dict):
                raise ValueError("superstep writes must be objects")
            op = str(raw.get("op") or "").strip().lower()
            if op == "task_release":
                task_id = str(raw.get("task_id") or "").strip()
                if not task_id:
                    raise ValueError("task_release requires task_id")
                status = str(raw.get("status") or "done").strip().lower()
                if status not in {"ready", "review", "done"}:
                    raise ValueError(
                        "superstep task_release status must be ready, review, or done; "
                        "use superstep_fail for failed/blocked participants"
                    )
                normalized.append({
                    "op": "task_release",
                    "task_id": task_id,
                    "status": status,
                    "summary": str(raw.get("summary") or "").strip(),
                    "result": raw.get("result"),
                })
            elif op == "status_set":
                key = str(raw.get("key") or "").strip()
                if not key:
                    raise ValueError("status_set requires key")
                normalized.append({
                    "op": "status_set",
                    "key": key,
                    "value": raw.get("value"),
                })
            else:
                raise ValueError("unsupported superstep write op: {0}".format(op or "<empty>"))
        json.dumps(normalized, ensure_ascii=False)
        return normalized

    @staticmethod
    def _superstep_write_target(write: Dict[str, object]) -> str:
        if write.get("op") == "task_release":
            return "task:{0}".format(write.get("task_id"))
        if write.get("op") == "status_set":
            return "status:{0}".format(write.get("key"))
        return "unknown"

    @staticmethod
    def _superstep_summary(step: Dict[str, object]) -> Dict[str, object]:
        participants = list(step.get("participants") or [])
        pending = step.get("pending_writes") or {}
        failures = step.get("failures") or {}
        staged = [name for name in participants if name in pending]
        return {
            "step_id": str(step.get("step_id") or ""),
            "status": str(step.get("status") or ""),
            "participants": participants,
            "staged_participants": staged,
            "missing_participants": [name for name in participants if name not in pending],
            "failures": dict(failures),
            "ready_to_commit": (
                str(step.get("status") or "") == "ready"
                and len(staged) == len(participants)
                and not failures
            ),
            "created_at": float(step.get("created_at") or 0.0),
            "committed_at": float(step.get("committed_at") or 0.0),
            "aborted_at": float(step.get("aborted_at") or 0.0),
            "metadata": step.get("metadata") or {},
            "commit_summary": step.get("commit_summary") or {},
            "error": str(step.get("error") or ""),
        }

    def superstep_begin(
        self,
        participants: List[str],
        step_id: str = "",
        metadata: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        names = []
        for item in participants or []:
            name = str(item or "").strip()
            if name and name not in names:
                names.append(name)
        if not names:
            raise ValueError("participants must be non-empty")
        clean_id = str(step_id or "").strip() or "step_" + uuid.uuid4().hex[:10]
        json.dumps(metadata or {}, ensure_ascii=False)
        with self._write_lock():
            data = self.load()
            if clean_id in data["supersteps"]:
                raise ValueError("step_id already exists")
            now = time.time()
            step = {
                "step_id": clean_id,
                "status": "open",
                "participants": names,
                "pending_writes": {},
                "failures": {},
                "metadata": dict(metadata or {}),
                "created_at": now,
                "committed_at": 0.0,
                "aborted_at": 0.0,
                "commit_summary": {},
                "error": "",
            }
            data["supersteps"][clean_id] = step
            self.save(data)
            return self._superstep_summary(step)

    def superstep_stage(
        self,
        step_id: str,
        worker_name: str,
        writes: List[Dict[str, object]],
    ) -> Dict[str, object]:
        clean_worker = str(worker_name or "").strip()
        if not clean_worker:
            raise ValueError("worker_name is required")
        normalized = self._normalize_superstep_writes(writes)
        with self._write_lock():
            data = self.load()
            step = data["supersteps"].get(str(step_id))
            if not isinstance(step, dict):
                raise KeyError("unknown step_id")
            if step.get("status") not in {"open", "ready"}:
                raise RuntimeError("superstep is not open")
            if clean_worker not in step.get("participants", []):
                raise RuntimeError("worker is not a superstep participant")
            if step.get("failures"):
                raise RuntimeError("superstep already has a participant failure")
            step["pending_writes"][clean_worker] = {
                "writes": normalized,
                "staged_at": time.time(),
            }
            participants = list(step.get("participants") or [])
            step["status"] = (
                "ready"
                if all(name in step["pending_writes"] for name in participants)
                else "open"
            )
            self.save(data)
            summary = self._superstep_summary(step)
            summary["worker_name"] = clean_worker
            summary["pending_write_count"] = len(normalized)
            return summary

    def superstep_fail(self, step_id: str, worker_name: str, error: str) -> Dict[str, object]:
        clean_worker = str(worker_name or "").strip()
        clean_error = str(error or "").strip()
        if not clean_worker:
            raise ValueError("worker_name is required")
        if not clean_error:
            raise ValueError("error is required")
        with self._write_lock():
            data = self.load()
            step = data["supersteps"].get(str(step_id))
            if not isinstance(step, dict):
                raise KeyError("unknown step_id")
            if step.get("status") not in {"open", "ready"}:
                raise RuntimeError("superstep is already terminal")
            if clean_worker not in step.get("participants", []):
                raise RuntimeError("worker is not a superstep participant")
            now = time.time()
            step["failures"][clean_worker] = {
                "error": clean_error,
                "failed_at": now,
            }
            step["status"] = "aborted"
            step["aborted_at"] = now
            step["error"] = "participant failure: {0}".format(clean_worker)
            self.save(data)
            return self._superstep_summary(step)

    def superstep_abort(
        self,
        step_id: str,
        reason: str = "aborted by orchestrator",
    ) -> Dict[str, object]:
        with self._write_lock():
            data = self.load()
            step = data["supersteps"].get(str(step_id))
            if not isinstance(step, dict):
                raise KeyError("unknown step_id")
            if step.get("status") in {"committed", "aborted", "conflict"}:
                return self._superstep_summary(step)
            now = time.time()
            step["status"] = "aborted"
            step["aborted_at"] = now
            step["error"] = str(reason or "aborted by orchestrator").strip()
            self.save(data)
            return self._superstep_summary(step)

    def _apply_superstep_write(
        self,
        data: Dict[str, object],
        step_id: str,
        worker_name: str,
        write: Dict[str, object],
        now: float,
    ) -> None:
        op = write.get("op")
        if op == "status_set":
            data["shared_status"][str(write["key"])] = {
                "value": write.get("value"),
                "updated_at": now,
            }
            return
        if op != "task_release":
            raise RuntimeError("unsupported pending write op")

        task = data["tasks"].get(str(write.get("task_id") or ""))
        if not isinstance(task, dict):
            raise RuntimeError("pending task no longer exists")
        if str(task.get("status") or "") != "active":
            raise RuntimeError("pending task is no longer active")
        if str(task.get("claimed_by") or "") != worker_name:
            raise RuntimeError("pending task lease owner changed")
        if str(task.get("superstep_id") or "") != str(step_id):
            raise RuntimeError("pending task superstep binding changed")
        if float(task.get("lease_expires_at") or 0.0) <= now:
            raise RuntimeError("pending task lease expired before commit")

        task["status"] = str(write.get("status") or "done")
        task["summary"] = str(write.get("summary") or "").strip()
        if "result" in write:
            task["result"] = write.get("result")
        task["last_error"] = ""
        task["retry_after"] = 0.0
        task["superstep_id"] = ""
        task["claimed_by"] = ""
        task["lease_expires_at"] = 0.0
        task["updated_at"] = now

    def superstep_commit(self, step_id: str) -> Dict[str, object]:
        with self._write_lock():
            data = self.load()
            step = data["supersteps"].get(str(step_id))
            if not isinstance(step, dict):
                raise KeyError("unknown step_id")
            if step.get("status") == "committed":
                return self._superstep_summary(step)
            if step.get("status") != "ready":
                raise RuntimeError("superstep is not ready to commit")
            if step.get("failures"):
                raise RuntimeError("superstep has participant failures")
            participants = list(step.get("participants") or [])
            pending = step.get("pending_writes") or {}
            missing = [name for name in participants if name not in pending]
            if missing:
                raise RuntimeError(
                    "superstep is missing staged participants: {0}".format(", ".join(missing))
                )

            seen_targets = {}
            for worker in participants:
                for write in pending[worker].get("writes", []):
                    target = self._superstep_write_target(write)
                    previous = seen_targets.get(target)
                    if previous and previous != worker:
                        now = time.time()
                        step["status"] = "conflict"
                        step["aborted_at"] = now
                        step["error"] = (
                            "conflicting pending writes for {0}: {1}, {2}".format(
                                target, previous, worker
                            )
                        )
                        self.save(data)
                        return self._superstep_summary(step)
                    seen_targets[target] = worker

            candidate = copy.deepcopy(data)
            candidate_step = candidate["supersteps"][str(step_id)]
            now = time.time()
            try:
                for worker in participants:
                    for write in pending[worker].get("writes", []):
                        self._apply_superstep_write(
                            candidate,
                            str(step_id),
                            worker,
                            write,
                            now,
                        )
            except Exception as exc:
                step["status"] = "conflict"
                step["aborted_at"] = now
                step["error"] = str(exc)
                self.save(data)
                return self._superstep_summary(step)

            candidate_step["status"] = "committed"
            candidate_step["committed_at"] = now
            candidate_step["error"] = ""
            candidate_step["commit_summary"] = {
                "participant_count": len(participants),
                "write_count": sum(
                    len(pending[worker].get("writes", [])) for worker in participants
                ),
                "targets": sorted(seen_targets),
            }
            self.save(candidate)
            return self._superstep_summary(candidate_step)

    def superstep_get(self, step_id: str, include_pending: bool = True) -> Dict[str, object]:
        step = self.load()["supersteps"].get(str(step_id))
        if not isinstance(step, dict):
            raise KeyError("unknown step_id")
        result = self._superstep_summary(step)
        if include_pending:
            result["pending_writes"] = copy.deepcopy(step.get("pending_writes") or {})
        return result

    def supersteps(self, include_pending: bool = False) -> List[Dict[str, object]]:
        values = []
        for step in self.load()["supersteps"].values():
            if not isinstance(step, dict):
                continue
            item = self._superstep_summary(step)
            if include_pending:
                item["pending_writes"] = copy.deepcopy(step.get("pending_writes") or {})
            values.append(item)
        return sorted(values, key=lambda item: (item["created_at"], item["step_id"]))

    def task_update(
        self,
        task_id: str,
        status: str = "",
        owner: str = "",
        summary: str = "",
    ) -> Dict[str, object]:
        with self._write_lock():
            data = self.load()
            task = data["tasks"].get(str(task_id))
            if not isinstance(task, dict):
                raise KeyError("unknown task_id")
            if status:
                clean_status = str(status).strip().lower()
                if clean_status not in {"ready", "active", "blocked", "review", "done", "failed"}:
                    raise ValueError("invalid task status")
                task["status"] = clean_status
            if owner:
                task["owner"] = str(owner).strip()
            if summary:
                task["summary"] = str(summary).strip()
            task["updated_at"] = time.time()
            self.save(data)
            return dict(task)

    def tasks(self) -> List[Dict[str, object]]:
        values = list(self.load()["tasks"].values())
        return sorted(
            [dict(item) for item in values if isinstance(item, dict)],
            key=lambda item: (str(item.get("status")), str(item.get("task_id"))),
        )

    def status_set(self, key: str, value: object) -> Dict[str, object]:
        clean = str(key or "").strip()
        if not clean:
            raise ValueError("status key is required")
        with self._write_lock():
            data = self.load()
            data["shared_status"][clean] = {
                "value": value,
                "updated_at": time.time(),
            }
            self.save(data)
            return dict(data["shared_status"][clean])

    def status(self) -> Dict[str, object]:
        return dict(self.load()["shared_status"])
