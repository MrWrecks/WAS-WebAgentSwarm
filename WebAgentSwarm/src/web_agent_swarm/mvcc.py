"""Filesystem MVCC lanes for Web Agent Swarm.

The durable registry is coordination metadata. A lane is the filesystem transaction
boundary: workers mutate a private copy, and only lane_commit publishes to canonical.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


LANE_LIVE = {"creating", "active", "committing", "recovery_required"}
CLAIM_LIVE = {"active"}
DEFAULT_IGNORES = {
    ".git",
    ".web-agent-swarm",
    ".ai-bridge",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path) -> Dict[str, object]:
    if path.is_symlink():
        target = os.readlink(str(path))
        return {
            "type": "symlink",
            "target": target,
            "sha256": hashlib.sha256(("symlink:" + target).encode("utf-8")).hexdigest(),
        }
    if path.is_file():
        return {
            "type": "file",
            "size": int(path.stat().st_size),
            "sha256": _sha256_file(path),
        }
    raise ValueError("manifest entry must be a file or symlink")


def _manifest(root: Path) -> Dict[str, Dict[str, object]]:
    result: Dict[str, Dict[str, object]] = {}
    for current, dirs, files in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in DEFAULT_IGNORES
            and not (current_path / name).is_symlink()
        )
        for name in sorted(files):
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if any(part in DEFAULT_IGNORES for part in Path(rel).parts):
                continue
            if path.is_symlink() or path.is_file():
                result[rel] = _entry(path)
    return result


def _hash_manifest(manifest: Dict[str, Dict[str, object]]) -> str:
    raw = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_workspace_id(root: Path) -> str:
    normalized = os.path.normcase(str(root.expanduser().resolve()))
    return "canon_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _normalize_relpath(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("resource path must stay inside the canonical workspace")
    return "/".join(part for part in path.parts if part not in {"", "."})


def normalize_resource(resource: str) -> Dict[str, str]:
    text = str(resource or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("resource is required")
    if ":" in text:
        namespace, tail = text.split(":", 1)
        namespace = namespace.strip().lower()
        if not namespace or "/" in namespace:
            raise ValueError("invalid resource namespace")
        tail = _normalize_relpath(tail)
        canonical = namespace + (":" + tail if tail else ":")
    else:
        namespace = "path"
        tail = _normalize_relpath(text)
        if not tail:
            raise ValueError("path resource cannot be empty")
        canonical = tail
    return {
        "resource": canonical,
        "namespace": namespace,
        "path": tail,
    }


def resources_overlap(left: Dict[str, object], right: Dict[str, object]) -> bool:
    if str(left.get("namespace") or "") != str(right.get("namespace") or ""):
        return False
    a = str(left.get("path") or "").strip("/")
    b = str(right.get("path") or "").strip("/")
    if not a or not b:
        return True
    a_parts = tuple(a.split("/"))
    b_parts = tuple(b.split("/"))
    shorter = min(len(a_parts), len(b_parts))
    return a_parts[:shorter] == b_parts[:shorter]


def _claim_conflicts(left: Dict[str, object], right: Dict[str, object]) -> bool:
    if not resources_overlap(left, right):
        return False
    left_mode = str(left.get("mode") or "write")
    right_mode = str(right.get("mode") or "write")
    modes = {left_mode, right_mode}
    if "scratch" in modes or modes == {"read"}:
        return False
    # Filesystem MVCC readers consume an immutable lane snapshot, so a path reader may
    # coexist with a path writer. If the writer commits first, the reader's versions/read-set
    # validation makes any later write commit fail closed. Non-filesystem resources (tool
    # sessions, OA views, benchmarks, etc.) remain pessimistically read/write exclusive.
    if (
        str(left.get("namespace") or "") == "path"
        and str(right.get("namespace") or "") == "path"
        and modes == {"read", "write"}
    ):
        return False
    return left_mode == "write" or right_mode == "write"


def _copy_workspace(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    def ignore(_dir: str, names: List[str]):
        return sorted(name for name in names if name in DEFAULT_IGNORES)

    shutil.copytree(
        str(source),
        str(target),
        symlinks=True,
        ignore=ignore,
    )


class LaneManager:
    def __init__(self, store) -> None:
        self.store = store
        self.recover_incomplete_commits()

    @staticmethod
    def _write_json_atomic(path: Path, value: Dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(str(temp), str(path))

    @staticmethod
    def _restore_journal(journal: Dict[str, object]) -> None:
        canonical = Path(str(journal.get("canonical_root") or "")).resolve()
        commit_id = str(journal.get("commit_id") or "")
        if not canonical.is_dir() or not commit_id:
            raise RuntimeError("commit recovery journal is incomplete")

        for rel in journal.get("write_paths") or []:
            rel = _normalize_relpath(str(rel))
            target = canonical / rel
            temp_target = target.with_name(target.name + ".mvcc-" + commit_id + ".tmp")
            if temp_target.is_symlink() or temp_target.is_file():
                try:
                    temp_target.unlink()
                except OSError:
                    pass

        for rel in journal.get("created_paths") or []:
            rel = _normalize_relpath(str(rel))
            target = canonical / rel
            if target.is_symlink() or target.is_file():
                target.unlink()

        backups = journal.get("backups") or {}
        if not isinstance(backups, dict):
            raise RuntimeError("commit recovery journal backups are invalid")
        for rel, backup in backups.items():
            if not isinstance(backup, dict):
                raise RuntimeError("commit recovery backup record is invalid")
            rel = _normalize_relpath(str(rel))
            target = canonical / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.is_file():
                target.unlink()
            if backup.get("type") == "symlink":
                os.symlink(str(backup.get("target") or ""), str(target))
            elif backup.get("type") == "file":
                source = Path(str(backup.get("path") or ""))
                if not source.is_file():
                    raise RuntimeError("commit recovery backup file is missing: {0}".format(rel))
                shutil.copy2(str(source), str(target))
            else:
                raise RuntimeError("commit recovery backup type is invalid")

    def recover_incomplete_commits(self) -> Dict[str, object]:
        recovered = []
        recovery_required = []
        with self.store._write_lock(timeout=30.0):
            data = self.store.load()
            now = time.time()
            self._clean_expired(data, now)
            changed = False
            for commit in data["commits"].values():
                if not isinstance(commit, dict) or commit.get("status") != "committing":
                    continue
                commit_id = str(commit.get("commit_id") or "")
                lane_id = str(commit.get("lane_id") or "")
                run_id = str(commit.get("run_id") or "")
                lane = data["lanes"].get(lane_id)
                run = data["runs"].get(run_id)
                journal_path = Path(str(commit.get("journal_path") or ""))
                try:
                    if not journal_path.is_file():
                        raise RuntimeError("commit recovery journal is missing")
                    journal = json.loads(journal_path.read_text(encoding="utf-8"))
                    if not isinstance(journal, dict):
                        raise RuntimeError("commit recovery journal is invalid")
                    self._restore_journal(journal)
                    commit["status"] = "recovered_rollback"
                    commit["error"] = "recovered incomplete filesystem commit after process restart"
                    commit["recovered_at"] = now
                    commit["updated_at"] = now
                    live_claims = self._claims_for_run(data, run_id)
                    next_status = "active" if live_claims else "aborted"
                    if isinstance(lane, dict):
                        lane["status"] = next_status
                        lane["error"] = commit["error"]
                        lane["updated_at"] = now
                    if isinstance(run, dict):
                        run["status"] = next_status
                        run["error"] = commit["error"]
                        run["updated_at"] = now
                    if not live_claims:
                        self._release_in_data(data, run_id, "released_recovery", now)
                    recovered.append(commit_id)
                except Exception as exc:
                    commit["status"] = "recovery_required"
                    commit["error"] = str(exc)
                    commit["updated_at"] = now
                    if isinstance(lane, dict):
                        lane["status"] = "recovery_required"
                        lane["error"] = str(exc)
                        lane["updated_at"] = now
                    if isinstance(run, dict):
                        run["status"] = "recovery_required"
                        run["error"] = str(exc)
                        run["updated_at"] = now
                    recovery_required.append(
                        {"commit_id": commit_id, "error": str(exc)}
                    )
                changed = True
            if changed:
                self.store.save(data)
        return {
            "recovered_commit_ids": recovered,
            "recovery_required": recovery_required,
        }

    @property
    def lanes_root(self) -> Path:
        return self.store.root / "lanes"

    def _manifest_path(self, lane: Dict[str, object]) -> Path:
        return Path(str(lane["lane_root"])) / "base-manifest.json"

    def _workspace_path(self, lane: Dict[str, object]) -> Path:
        return Path(str(lane["workspace_path"]))

    def _load_manifest(self, lane: Dict[str, object]) -> Dict[str, Dict[str, object]]:
        path = self._manifest_path(lane)
        if not path.is_file():
            raise RuntimeError("lane base manifest is missing")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("lane base manifest is invalid")
        return value

    @staticmethod
    def _clean_expired(data: Dict[str, object], now: float) -> None:
        for claim in data["resource_claims"].values():
            if not isinstance(claim, dict):
                continue
            if (
                claim.get("status") == "active"
                and float(claim.get("lease_expires_at") or 0.0) <= now
            ):
                claim["status"] = "expired"
                claim["updated_at"] = now

    @staticmethod
    def _task_owner(data: Dict[str, object], task_id: str, worker_name: str, now: float):
        task = data["tasks"].get(str(task_id))
        if not isinstance(task, dict):
            raise KeyError("unknown task_id")
        if str(task.get("status") or "") != "active":
            raise RuntimeError("task must be active before starting a lane")
        if str(task.get("claimed_by") or "") != str(worker_name):
            raise RuntimeError("worker does not own the task lease")
        if float(task.get("lease_expires_at") or 0.0) <= now:
            raise RuntimeError("task lease expired before lane creation")
        return task

    @staticmethod
    def _validate_assist_claim_modes(task: Dict[str, object], resources: Iterable[object]) -> None:
        policy = str(task.get("assist_write_policy") or "").strip().lower()
        if not policy:
            return
        modes = []
        for raw in resources:
            if isinstance(raw, dict):
                modes.append(str(raw.get("mode") or "write").strip().lower())
            else:
                modes.append("write")
        if policy in {"readonly", "proposal_only", "experiment_only"} and "write" in modes:
            raise RuntimeError(
                "sidecar write policy {0} forbids canonical write claims; use read/scratch claims "
                "or return a proposal/result to the primary writer".format(policy)
            )

    @staticmethod
    def _claims_for_run(data: Dict[str, object], run_id: str) -> List[Dict[str, object]]:
        now = time.time()
        return [
            claim
            for claim in data["resource_claims"].values()
            if isinstance(claim, dict)
            and str(claim.get("run_id") or "") == str(run_id)
            and claim.get("status") == "active"
            and float(claim.get("lease_expires_at") or 0.0) > now
        ]

    def _reserve_claims(
        self,
        data: Dict[str, object],
        run_id: str,
        lane_id: str,
        resources: Iterable[object],
        lease_seconds: int,
        now: float,
        canonical_id: str,
    ) -> List[Dict[str, object]]:
        requested: List[Dict[str, object]] = []
        for raw in resources:
            if isinstance(raw, dict):
                spec = normalize_resource(str(raw.get("resource") or ""))
                mode = str(raw.get("mode") or "write").strip().lower()
            else:
                spec = normalize_resource(str(raw))
                mode = "write"
            if mode not in {"read", "write", "scratch"}:
                raise ValueError("resource claim mode must be read, write, or scratch")
            spec["mode"] = mode
            requested.append(spec)
        if not requested:
            raise ValueError("at least one resource claim is required")

        existing = [
            claim
            for claim in data["resource_claims"].values()
            if isinstance(claim, dict)
            and claim.get("status") == "active"
            and float(claim.get("lease_expires_at") or 0.0) > now
            and str(claim.get("run_id") or "") != run_id
            and str(claim.get("canonical_id") or "") == canonical_id
        ]
        conflicts = []
        for candidate in requested:
            for claim in existing:
                if _claim_conflicts(candidate, claim):
                    conflicts.append(
                        {
                            "requested": candidate["resource"],
                            "requested_mode": candidate["mode"],
                            "held": str(claim.get("resource") or ""),
                            "held_mode": str(claim.get("mode") or ""),
                            "held_by_run": str(claim.get("run_id") or ""),
                        }
                    )
        if conflicts:
            raise RuntimeError(
                "resource claim conflict: " + json.dumps(conflicts, ensure_ascii=False)
            )

        records = []
        for candidate in requested:
            claim_id = "claim_" + uuid.uuid4().hex[:12]
            record = {
                "claim_id": claim_id,
                "run_id": run_id,
                "lane_id": lane_id,
                "canonical_id": canonical_id,
                "resource": candidate["resource"],
                "namespace": candidate["namespace"],
                "path": candidate["path"],
                "mode": candidate["mode"],
                "status": "active",
                "lease_expires_at": now + max(1, int(lease_seconds or 1)),
                "created_at": now,
                "updated_at": now,
            }
            data["resource_claims"][claim_id] = record
            records.append(record)
        return records

    def start(
        self,
        task_id: str,
        worker_name: str,
        canonical_root: str,
        resources: List[object],
        lease_seconds: int = 900,
        run_id: str = "",
        parent_run_id: str = "",
    ) -> Dict[str, object]:
        canonical = Path(str(canonical_root or "")).expanduser().resolve()
        if not canonical.is_dir():
            raise ValueError("canonical_root must be an existing directory")
        clean_worker = str(worker_name or "").strip()
        if not clean_worker:
            raise ValueError("worker_name is required")
        clean_run = str(run_id or "").strip() or "run_" + uuid.uuid4().hex[:12]
        lane_id = "lane_" + uuid.uuid4().hex[:12]
        lane_root = self.lanes_root / lane_id
        workspace = lane_root / "workspace"
        canonical_id = canonical_workspace_id(canonical)
        now = time.time()

        with self.store._write_lock():
            data = self.store.load()
            self._clean_expired(data, now)
            task = self._task_owner(data, task_id, clean_worker, now)
            self._validate_assist_claim_modes(task, resources)
            if clean_run in data["runs"]:
                raise ValueError("run_id already exists")
            claims = self._reserve_claims(
                data,
                clean_run,
                lane_id,
                resources,
                lease_seconds,
                now,
                canonical_id,
            )
            base_epoch = int(data.get("canonical_epochs", {}).get(canonical_id) or 0)
            data["runs"][clean_run] = {
                "run_id": clean_run,
                "task_id": str(task_id),
                "worker_name": clean_worker,
                "lane_id": lane_id,
                "parent_run_id": str(parent_run_id or ""),
                "canonical_root": str(canonical),
                "canonical_id": canonical_id,
                "base_epoch": base_epoch,
                "status": "creating",
                "started_at": now,
                "updated_at": now,
            }
            data["lanes"][lane_id] = {
                "lane_id": lane_id,
                "run_id": clean_run,
                "task_id": str(task_id),
                "worker_name": clean_worker,
                "canonical_root": str(canonical),
                "canonical_id": canonical_id,
                "lane_root": str(lane_root),
                "workspace_path": str(workspace),
                "base_epoch": base_epoch,
                "base_manifest_sha256": "",
                "base_file_count": 0,
                "read_set": {},
                "write_set": {},
                "opaque_reads": False,
                "status": "creating",
                "created_at": now,
                "updated_at": now,
            }
            self.store.save(data)

        try:
            _copy_workspace(canonical, workspace)
            base_manifest = _manifest(workspace)
            canonical_manifest = _manifest(canonical)
            if _hash_manifest(base_manifest) != _hash_manifest(canonical_manifest):
                raise RuntimeError(
                    "canonical workspace changed while the MVCC snapshot was being created; retry lane_start"
                )
            lane_root.mkdir(parents=True, exist_ok=True)
            self._manifest_path(
                {"lane_root": str(lane_root)}
            ).write_text(
                json.dumps(base_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_sha = _hash_manifest(base_manifest)
        except Exception:
            self.abort(lane_id, reason="lane creation failed", cleanup=True)
            raise

        with self.store._write_lock():
            data = self.store.load()
            lane = data["lanes"].get(lane_id)
            run = data["runs"].get(clean_run)
            if not isinstance(lane, dict) or not isinstance(run, dict):
                raise RuntimeError("lane registry disappeared during creation")
            lane["status"] = "active"
            lane["base_manifest_sha256"] = manifest_sha
            lane["base_file_count"] = len(base_manifest)
            lane["updated_at"] = time.time()
            run["status"] = "active"
            run["updated_at"] = lane["updated_at"]
            self.store.save(data)
            return {
                "run": copy.deepcopy(run),
                "lane": copy.deepcopy(lane),
                "claims": copy.deepcopy(claims),
            }

    def get(self, lane_id: str) -> Dict[str, object]:
        data = self.store.load()
        lane = data["lanes"].get(str(lane_id))
        if not isinstance(lane, dict):
            raise KeyError("unknown lane_id")
        run = data["runs"].get(str(lane.get("run_id") or "")) or {}
        claims = self._claims_for_run(data, str(lane.get("run_id") or ""))
        return {
            "lane": copy.deepcopy(lane),
            "run": copy.deepcopy(run),
            "claims": copy.deepcopy(claims),
        }

    def list(self, status: str = "") -> List[Dict[str, object]]:
        data = self.store.load()
        values = []
        for lane in data["lanes"].values():
            if not isinstance(lane, dict):
                continue
            if status and str(lane.get("status") or "") != str(status):
                continue
            values.append(copy.deepcopy(lane))
        return sorted(
            values,
            key=lambda item: (float(item.get("created_at") or 0.0), str(item.get("lane_id") or "")),
        )

    def runs(self, status: str = "") -> List[Dict[str, object]]:
        values = []
        for run in self.store.load()["runs"].values():
            if not isinstance(run, dict):
                continue
            if status and str(run.get("status") or "") != str(status):
                continue
            values.append(copy.deepcopy(run))
        return sorted(
            values,
            key=lambda item: (float(item.get("started_at") or 0.0), str(item.get("run_id") or "")),
        )

    def claims(self, status: str = "") -> List[Dict[str, object]]:
        values = []
        for claim in self.store.load()["resource_claims"].values():
            if not isinstance(claim, dict):
                continue
            if status and str(claim.get("status") or "") != str(status):
                continue
            values.append(copy.deepcopy(claim))
        return sorted(
            values,
            key=lambda item: (float(item.get("created_at") or 0.0), str(item.get("claim_id") or "")),
        )

    def commits(self, status: str = "") -> List[Dict[str, object]]:
        values = []
        for commit in self.store.load()["commits"].values():
            if not isinstance(commit, dict):
                continue
            if status and str(commit.get("status") or "") != str(status):
                continue
            values.append(copy.deepcopy(commit))
        return sorted(
            values,
            key=lambda item: (float(item.get("created_at") or 0.0), str(item.get("commit_id") or "")),
        )

    def claim_resource(
        self,
        run_id: str,
        resource: str,
        mode: str = "write",
        lease_seconds: int = 900,
    ) -> Dict[str, object]:
        now = time.time()
        with self.store._write_lock():
            data = self.store.load()
            self._clean_expired(data, now)
            run = data["runs"].get(str(run_id))
            if not isinstance(run, dict) or run.get("status") not in {"active", "creating"}:
                raise RuntimeError("run is not active")
            lane_id = str(run.get("lane_id") or "")
            records = self._reserve_claims(
                data,
                str(run_id),
                lane_id,
                [{"resource": resource, "mode": mode}],
                lease_seconds,
                now,
                str(run.get("canonical_id") or ""),
            )
            self.store.save(data)
            return copy.deepcopy(records[0])

    def heartbeat(self, lane_id: str, lease_seconds: int = 900) -> Dict[str, object]:
        now = time.time()
        with self.store._write_lock():
            data = self.store.load()
            lane = data["lanes"].get(str(lane_id))
            if not isinstance(lane, dict) or lane.get("status") not in LANE_LIVE:
                raise RuntimeError("lane is not live")
            run_id = str(lane.get("run_id") or "")
            renewed = []
            for claim in self._claims_for_run(data, run_id):
                claim["lease_expires_at"] = now + max(1, int(lease_seconds or 1))
                claim["updated_at"] = now
                renewed.append(str(claim.get("claim_id") or ""))
            lane["updated_at"] = now
            run = data["runs"].get(run_id)
            if isinstance(run, dict):
                run["updated_at"] = now
            self.store.save(data)
            return {
                "lane_id": str(lane_id),
                "run_id": run_id,
                "renewed_claim_ids": renewed,
                "lease_expires_at": now + max(1, int(lease_seconds or 1)),
            }

    def release_claims(self, run_id: str, status: str = "released") -> List[str]:
        now = time.time()
        released = []
        with self.store._write_lock():
            data = self.store.load()
            for claim in data["resource_claims"].values():
                if (
                    isinstance(claim, dict)
                    and str(claim.get("run_id") or "") == str(run_id)
                    and claim.get("status") == "active"
                ):
                    claim["status"] = status
                    claim["updated_at"] = now
                    released.append(str(claim.get("claim_id") or ""))
            self.store.save(data)
        return released

    def abort(self, lane_id: str, reason: str = "aborted", cleanup: bool = False) -> Dict[str, object]:
        now = time.time()
        lane_root = None
        run_id = ""
        with self.store._write_lock():
            data = self.store.load()
            lane = data["lanes"].get(str(lane_id))
            if not isinstance(lane, dict):
                raise KeyError("unknown lane_id")
            if lane.get("status") == "recovery_required":
                raise RuntimeError(
                    "lane requires commit recovery before it can be aborted"
                )
            run_id = str(lane.get("run_id") or "")
            lane_root = Path(str(lane.get("lane_root") or ""))
            lane["status"] = "aborted"
            lane["error"] = str(reason or "aborted")
            lane["updated_at"] = now
            run = data["runs"].get(run_id)
            if isinstance(run, dict):
                run["status"] = "aborted"
                run["error"] = lane["error"]
                run["updated_at"] = now
            for claim in data["resource_claims"].values():
                if (
                    isinstance(claim, dict)
                    and str(claim.get("run_id") or "") == run_id
                    and claim.get("status") == "active"
                ):
                    claim["status"] = "released"
                    claim["updated_at"] = now
            self.store.save(data)
        if cleanup and lane_root and lane_root.is_dir():
            shutil.rmtree(lane_root, ignore_errors=True)
        return {"lane_id": str(lane_id), "run_id": run_id, "status": "aborted"}

    @staticmethod
    def _path_under(path: Path, root: Path) -> Optional[str]:
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            return None
        return rel.as_posix()

    def classify_workspace(self, root: Path) -> Dict[str, object]:
        root = root.expanduser().resolve()
        data = self.store.load()
        now = time.time()
        lane_candidates = [
            lane
            for lane in data["lanes"].values()
            if isinstance(lane, dict) and lane.get("status") in LANE_LIVE
        ]
        for lane in lane_candidates:
            workspace = Path(str(lane.get("workspace_path") or "")).resolve()
            rel = self._path_under(root, workspace)
            if rel is not None:
                return {
                    "kind": "lane",
                    "lane": lane,
                    "workspace_prefix": "" if rel == "." else rel,
                }

        protected_run_ids = {
            str(claim.get("run_id") or "")
            for claim in data["resource_claims"].values()
            if isinstance(claim, dict)
            and claim.get("status") == "active"
            and float(claim.get("lease_expires_at") or 0.0) > now
        }
        canonical_lanes = [
            lane
            for lane in lane_candidates
            if lane.get("status") in {"committing", "recovery_required"}
            or str(lane.get("run_id") or "") in protected_run_ids
        ]
        for lane in canonical_lanes:
            canonical = Path(str(lane.get("canonical_root") or "")).resolve()
            rel = self._path_under(root, canonical)
            if rel is not None:
                return {
                    "kind": "canonical",
                    "lane": lane,
                    "workspace_prefix": "" if rel == "." else rel,
                    "canonical_root": str(canonical),
                }
        return {"kind": "unmanaged"}

    def _lane_path_mode(self, data: Dict[str, object], lane: Dict[str, object], rel: str) -> str:
        path_spec = normalize_resource(rel)
        target_path = str(path_spec.get("path") or "").strip("/")
        target_parts = tuple(target_path.split("/")) if target_path else ()
        run_id = str(lane.get("run_id") or "")
        matches = []
        for claim in self._claims_for_run(data, run_id):
            mode = str(claim.get("mode") or "")
            if mode not in {"write", "scratch"}:
                continue
            if str(claim.get("namespace") or "") != "path":
                continue
            claim_path = str(claim.get("path") or "").strip("/")
            claim_parts = tuple(claim_path.split("/")) if claim_path else ()
            if not claim_parts or target_parts[: len(claim_parts)] == claim_parts:
                matches.append((len(claim_parts), 1 if mode == "write" else 0, mode))
        if not matches:
            return ""
        matches.sort(reverse=True)
        return matches[0][2]

    def _lane_claim_covers(self, data: Dict[str, object], lane: Dict[str, object], rel: str) -> bool:
        return self._lane_path_mode(data, lane, rel) in {"write", "scratch"}

    def guard_mutation(self, root: Path, relative_path: str = "", operation: str = "mutation") -> Dict[str, object]:
        info = self.classify_workspace(root)
        if info["kind"] == "canonical":
            raise RuntimeError(
                "canonical workspace is MVCC-protected while a lane is active; mutate the lane and commit instead"
            )
        if info["kind"] != "lane":
            return info
        lane = info["lane"]
        if lane.get("status") != "active":
            raise RuntimeError(
                "MVCC lane is not mutable in status {0}".format(
                    str(lane.get("status") or "")
                )
            )
        prefix = str(info.get("workspace_prefix") or "").strip("/")
        rel = _normalize_relpath(relative_path)
        effective = "/".join(part for part in [prefix, rel] if part)
        if not effective:
            # bash/apply_patch may touch multiple paths. The lane is still safe, and
            # commit derives the complete write-set from the base manifest.
            return info
        data = self.store.load()
        if not self._lane_claim_covers(data, lane, effective):
            raise RuntimeError(
                "lane mutation is outside the run's hierarchical write claims: {0}".format(effective)
            )
        self.record_write(str(lane.get("lane_id") or ""), effective, operation=operation)
        return info

    def guard_read(self, root: Path, relative_path: str = "") -> Dict[str, object]:
        info = self.classify_workspace(root)
        if (
            info["kind"] == "canonical"
            and info["lane"].get("status") in {"committing", "recovery_required"}
        ):
            raise RuntimeError("canonical workspace is unavailable during MVCC commit recovery")
        if info["kind"] == "lane":
            lane = info["lane"]
            prefix = str(info.get("workspace_prefix") or "").strip("/")
            rel = _normalize_relpath(relative_path)
            effective = "/".join(part for part in [prefix, rel] if part)
            if effective:
                self.record_read(str(lane.get("lane_id") or ""), effective)
        return info

    def record_read(self, lane_id: str, relative_path: str) -> None:
        rel = _normalize_relpath(relative_path)
        with self.store._write_lock():
            data = self.store.load()
            lane = data["lanes"].get(str(lane_id))
            if not isinstance(lane, dict) or lane.get("status") != "active":
                return
            manifest = self._load_manifest(lane)
            base = manifest.get(rel)
            lane.setdefault("read_set", {})[rel] = copy.deepcopy(base)
            lane["updated_at"] = time.time()
            self.store.save(data)

    def record_write(self, lane_id: str, relative_path: str, operation: str = "write") -> None:
        rel = _normalize_relpath(relative_path)
        with self.store._write_lock():
            data = self.store.load()
            lane = data["lanes"].get(str(lane_id))
            if not isinstance(lane, dict) or lane.get("status") != "active":
                return
            manifest = self._load_manifest(lane)
            lane.setdefault("write_set", {})[rel] = {
                "base": copy.deepcopy(manifest.get(rel)),
                "operation": str(operation or "write"),
                "updated_at": time.time(),
            }
            lane["updated_at"] = time.time()
            self.store.save(data)

    def record_opaque_read(self, root: Path, command: str = "") -> Dict[str, object]:
        info = self.classify_workspace(root)
        if (
            info["kind"] == "canonical"
            and info["lane"].get("status") in {"committing", "recovery_required"}
        ):
            raise RuntimeError("canonical workspace is unavailable during MVCC commit recovery")
        if info["kind"] != "lane":
            return info
        if info["lane"].get("status") != "active":
            raise RuntimeError(
                "MVCC lane is not observable through active tools in status {0}".format(
                    str(info["lane"].get("status") or "")
                )
            )
        lane_id = str(info["lane"].get("lane_id") or "")
        with self.store._write_lock():
            data = self.store.load()
            lane = data["lanes"].get(lane_id)
            if isinstance(lane, dict):
                lane["opaque_reads"] = True
                lane.setdefault("commands", []).append(
                    {"command": str(command or ""), "at": time.time()}
                )
                lane["updated_at"] = time.time()
                self.store.save(data)
        return info

    @staticmethod
    def _current_entry(root: Path, rel: str) -> Optional[Dict[str, object]]:
        path = root / rel
        if path.is_symlink() or path.is_file():
            return _entry(path)
        return None

    @staticmethod
    def _entry_equal(left: object, right: object) -> bool:
        return left == right

    def _release_in_data(self, data: Dict[str, object], run_id: str, status: str, now: float):
        for claim in data["resource_claims"].values():
            if (
                isinstance(claim, dict)
                and str(claim.get("run_id") or "") == run_id
                and claim.get("status") == "active"
            ):
                claim["status"] = status
                claim["updated_at"] = now

    def commit(self, lane_id: str) -> Dict[str, object]:
        clean_lane = str(lane_id or "").strip()
        if not clean_lane:
            raise ValueError("lane_id is required")
        commit_id = "commit_" + uuid.uuid4().hex[:12]
        now = time.time()

        with self.store._write_lock(timeout=30.0):
            data = self.store.load()
            self._clean_expired(data, now)
            lane = data["lanes"].get(clean_lane)
            if not isinstance(lane, dict):
                raise KeyError("unknown lane_id")
            if lane.get("status") != "active":
                raise RuntimeError("lane is not active")
            run_id = str(lane.get("run_id") or "")
            run = data["runs"].get(run_id)
            if not isinstance(run, dict):
                raise RuntimeError("lane run is missing")
            claims = self._claims_for_run(data, run_id)
            if not claims:
                raise RuntimeError("run has no live resource claims")
            canonical = Path(str(lane.get("canonical_root") or "")).resolve()
            canonical_id = str(lane.get("canonical_id") or canonical_workspace_id(canonical))
            workspace = Path(str(lane.get("workspace_path") or "")).resolve()
            base_manifest = self._load_manifest(lane)
            lane_manifest = _manifest(workspace)

            paths = sorted(set(base_manifest) | set(lane_manifest))
            all_deltas = {
                rel: {
                    "base": copy.deepcopy(base_manifest.get(rel)),
                    "candidate": copy.deepcopy(lane_manifest.get(rel)),
                }
                for rel in paths
                if not self._entry_equal(base_manifest.get(rel), lane_manifest.get(rel))
            }
            write_set = {}
            scratch_set = {}
            unclaimed = []
            for rel, delta in all_deltas.items():
                mode = self._lane_path_mode(data, lane, rel)
                if mode == "write":
                    write_set[rel] = delta
                elif mode == "scratch":
                    scratch_set[rel] = delta
                else:
                    unclaimed.append(rel)
            if unclaimed:
                raise RuntimeError(
                    "candidate changes escaped hierarchical resource claims: {0}".format(
                        ",".join(sorted(unclaimed))
                    )
                )

            read_set = dict(lane.get("read_set") or {})
            validation_paths = set(write_set) | set(read_set)
            if lane.get("opaque_reads"):
                validation_paths.update(base_manifest)

            conflicts = []
            for rel in sorted(validation_paths):
                expected = base_manifest.get(rel)
                current = self._current_entry(canonical, rel)
                if not self._entry_equal(expected, current):
                    conflicts.append(
                        {
                            "path": rel,
                            "expected": copy.deepcopy(expected),
                            "current": copy.deepcopy(current),
                            "kind": "write" if rel in write_set else "read",
                        }
                    )

            commit_record = {
                "commit_id": commit_id,
                "run_id": run_id,
                "lane_id": clean_lane,
                "canonical_id": canonical_id,
                "base_epoch": int(lane.get("base_epoch") or 0),
                "status": "validating",
                "write_paths": sorted(write_set),
                "scratch_paths": sorted(scratch_set),
                "read_paths": sorted(read_set),
                "opaque_reads": bool(lane.get("opaque_reads")),
                "conflicts": copy.deepcopy(conflicts),
                "created_at": now,
                "updated_at": now,
            }
            data["commits"][commit_id] = commit_record

            if conflicts:
                commit_record["status"] = "conflict"
                lane["status"] = "conflict"
                lane["conflicts"] = copy.deepcopy(conflicts)
                lane["updated_at"] = now
                run["status"] = "conflict"
                run["updated_at"] = now
                self._release_in_data(data, run_id, "released_conflict", now)
                self.store.save(data)
                return {
                    "ok": False,
                    "status": "conflict",
                    "commit_id": commit_id,
                    "run_id": run_id,
                    "lane_id": clean_lane,
                    "conflicts": conflicts,
                    "write_paths": sorted(write_set),
                    "scratch_paths": sorted(scratch_set),
                }

            backup_root = Path(str(lane.get("lane_root") or "")) / "commit-backup" / commit_id
            backups: Dict[str, Dict[str, object]] = {}
            created_paths: List[str] = []
            journal_path = backup_root / "journal.json"
            journal = None
            try:
                for rel in sorted(write_set):
                    target = canonical / rel
                    current = self._current_entry(canonical, rel)
                    if current is not None:
                        backup = backup_root / rel
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        if target.is_symlink():
                            backups[rel] = {
                                "type": "symlink",
                                "target": os.readlink(str(target)),
                            }
                        else:
                            shutil.copy2(str(target), str(backup))
                            backups[rel] = {"type": "file", "path": str(backup)}
                    else:
                        created_paths.append(rel)

                journal = {
                    "commit_id": commit_id,
                    "run_id": run_id,
                    "lane_id": clean_lane,
                    "canonical_id": canonical_id,
                    "canonical_root": str(canonical),
                    "status": "prepared",
                    "write_paths": sorted(write_set),
                    "scratch_paths": sorted(scratch_set),
                    "backups": copy.deepcopy(backups),
                    "created_paths": sorted(created_paths),
                    "prepared_at": time.time(),
                }
                self._write_json_atomic(journal_path, journal)
                lane["status"] = "committing"
                run["status"] = "committing"
                commit_record["status"] = "committing"
                commit_record["journal_path"] = str(journal_path)
                self.store.save(data)

                for rel, delta in sorted(write_set.items()):
                    source = workspace / rel
                    target = canonical / rel
                    candidate = delta["candidate"]
                    if candidate is None:
                        if target.is_symlink() or target.is_file():
                            target.unlink()
                        continue
                    if candidate.get("type") == "symlink":
                        link_target = str(candidate.get("target") or "")
                        if os.path.isabs(link_target):
                            raise RuntimeError("absolute symlink writes are not supported")
                        resolved = (target.parent / link_target).resolve()
                        try:
                            resolved.relative_to(canonical)
                        except ValueError as exc:
                            raise RuntimeError("symlink target escapes canonical workspace") from exc
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists() or target.is_symlink():
                            target.unlink()
                        os.symlink(link_target, str(target))
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp_target = target.with_name(target.name + ".mvcc-" + commit_id + ".tmp")
                    shutil.copy2(str(source), str(temp_target))
                    os.replace(str(temp_target), str(target))

            except Exception as exc:
                for rel in created_paths:
                    target = canonical / rel
                    if target.is_symlink() or target.is_file():
                        try:
                            target.unlink()
                        except OSError:
                            pass
                for rel, backup in backups.items():
                    target = canonical / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        if target.is_symlink() or target.is_file():
                            target.unlink()
                        if backup["type"] == "symlink":
                            os.symlink(str(backup["target"]), str(target))
                        else:
                            shutil.copy2(str(backup["path"]), str(target))
                    except OSError:
                        pass
                if isinstance(journal, dict):
                    journal["status"] = "rolled_back"
                    journal["error"] = str(exc)
                    journal["rolled_back_at"] = time.time()
                    try:
                        self._write_json_atomic(journal_path, journal)
                    except OSError:
                        pass
                commit_record["status"] = "failed"
                commit_record["error"] = str(exc)
                commit_record["updated_at"] = time.time()
                lane["status"] = "active"
                run["status"] = "active"
                lane["updated_at"] = commit_record["updated_at"]
                run["updated_at"] = commit_record["updated_at"]
                self.store.save(data)
                raise

            after = time.time()
            per_canonical = data.setdefault("canonical_epochs", {})
            if write_set:
                data["canonical_epoch"] = int(data.get("canonical_epoch") or 0) + 1
                global_epoch = int(data["canonical_epoch"])
                per_canonical[canonical_id] = int(per_canonical.get(canonical_id) or 0) + 1
            else:
                global_epoch = int(data.get("canonical_epoch") or 0)
                per_canonical.setdefault(canonical_id, 0)
            epoch = int(per_canonical[canonical_id])
            for rel, delta in write_set.items():
                current = delta["candidate"]
                version_key = canonical_id + "::" + rel
                record = data["resource_versions"].setdefault(
                    version_key,
                    {
                        "resource": rel,
                        "canonical_id": canonical_id,
                        "version": 0,
                    },
                )
                record["version"] = int(record.get("version") or 0) + 1
                record["sha256"] = (
                    str(current.get("sha256") or "") if isinstance(current, dict) else ""
                )
                record["exists"] = current is not None
                record["changed_by_run"] = run_id
                record["canonical_epoch"] = epoch
                record["global_epoch"] = global_epoch
                record["updated_at"] = after

            commit_record["status"] = "committed"
            commit_record["canonical_epoch_after"] = epoch
            commit_record["global_epoch_after"] = global_epoch
            commit_record["updated_at"] = after
            lane["status"] = "committed"
            lane["write_set"] = copy.deepcopy(write_set)
            lane["scratch_set"] = copy.deepcopy(scratch_set)
            lane["committed_at"] = after
            lane["updated_at"] = after
            run["status"] = "committed"
            run["committed_at"] = after
            run["updated_at"] = after
            self._release_in_data(data, run_id, "released_commit", after)
            self.store.save(data)
            if isinstance(journal, dict):
                journal["status"] = "committed"
                journal["committed_at"] = after
                try:
                    self._write_json_atomic(journal_path, journal)
                except OSError:
                    pass

            return {
                "ok": True,
                "status": "committed",
                "commit_id": commit_id,
                "run_id": run_id,
                "lane_id": clean_lane,
                "canonical_id": canonical_id,
                "canonical_epoch_after": epoch,
                "global_epoch_after": global_epoch,
                "write_paths": sorted(write_set),
                "scratch_paths": sorted(scratch_set),
                "read_paths": sorted(read_set),
                "opaque_reads": bool(lane.get("opaque_reads")),
            }

    def fork(
        self,
        lane_id: str,
        lease_seconds: int = 900,
    ) -> Dict[str, object]:
        previous = self.get(lane_id)
        lane = previous["lane"]
        run = previous["run"]
        if lane.get("status") not in {"conflict", "aborted"}:
            raise RuntimeError("only conflicted/aborted lanes can be forked")
        resources = [
            {
                "resource": claim["resource"],
                "mode": claim.get("mode") or "write",
            }
            for claim in previous["claims"]
        ]
        if not resources:
            data = self.store.load()
            resources = [
                {
                    "resource": claim["resource"],
                    "mode": claim.get("mode") or "write",
                }
                for claim in data["resource_claims"].values()
                if isinstance(claim, dict)
                and str(claim.get("run_id") or "") == str(run.get("run_id") or "")
            ]
        return self.start(
            task_id=str(run.get("task_id") or ""),
            worker_name=str(run.get("worker_name") or ""),
            canonical_root=str(run.get("canonical_root") or ""),
            resources=resources,
            lease_seconds=lease_seconds,
            parent_run_id=str(run.get("run_id") or ""),
        )
