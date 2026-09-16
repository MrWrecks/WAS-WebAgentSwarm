"""Deterministic scheduler episodes inspired by BrowserGym/AgentLab benchmarks."""

import hashlib
import json
import platform
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List

from . import __version__
from .state import DurableStore, default_state_dir
from .swarm import SwarmRuntime

BENCHMARK_REVISION = "was-scheduler-v2"
SOURCE_FILES = ("roles.py", "state.py", "swarm.py", "mvcc.py", "watcher.py", "benchmark.py")


def _scheduler_source_hash() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for name in SOURCE_FILES:
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _journal_path(path: str = "") -> Path:
    return (
        Path(path).expanduser().resolve()
        if str(path or "").strip()
        else default_state_dir() / "benchmark_history.jsonl"
    )


def benchmark_history(limit: int = 20, journal_path: str = "") -> Dict[str, object]:
    path = _journal_path(journal_path)
    keep = max(1, min(int(limit or 1), 500))
    if not path.is_file():
        return {"ok": True, "path": str(path), "runs": []}
    runs = []
    for raw in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            runs.append(item)
        if len(runs) >= keep:
            break
    return {"ok": True, "path": str(path), "runs": runs}


def _record_benchmark(result: Dict[str, object], journal_path: str = "") -> str:
    path = _journal_path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return str(path)


def _plan_metrics(plan: Dict[str, object]) -> Dict[str, object]:
    recommendation = plan.get("scale_recommendation") or {}
    recommended = dict(recommendation.get("recommended_instances") or {})
    return {
        "parallel_frontier": [
            str(item.get("task_id") or "")
            for item in plan.get("parallel_frontier", [])
            if isinstance(item, dict)
        ],
        "parallel_sidecars": [
            str(item.get("task_id") or "")
            for item in plan.get("parallel_sidecars", [])
            if isinstance(item, dict)
        ],
        "recommended_instances": recommended,
        "recommended_worker_count": sum(int(v or 0) for v in recommended.values()),
        "wait_routes": [
            str(item.get("blocker_kind") or item.get("kind") or "")
            for item in plan.get("waits", [])
            if isinstance(item, dict)
        ],
    }


def _episode(name: str, setup) -> Dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        store = DurableStore(Path(tmp))
        runtime = SwarmRuntime(store=store)
        started = time.perf_counter()
        result = setup(store, runtime)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result.update({
            "name": name,
            "revision": BENCHMARK_REVISION,
            "elapsed_ms": round(elapsed_ms, 3),
        })
        return result


def run_scheduler_benchmark(
    record: bool = False,
    journal_path: str = "",
) -> Dict[str, object]:
    episodes: List[Dict[str, object]] = []

    def single_primary(store, runtime):
        store.task_create(
            "Primary implementation",
            owner="implementer",
            task_id="primary",
            resource_hints=["src/core.py"],
            canonical_root=".",
        )
        parallelism = runtime.ensure_parallelism(
            max_sidecars_per_primary=2,
            max_new_sidecars=4,
        )
        plan = runtime.coordination_plan(active_roles=[], max_total_workers=6)
        metrics = _plan_metrics(plan)
        policies = {
            str(task.get("assist_write_policy") or "")
            for task in store.task_assists("primary", include_terminal=True)
        }
        passed = (
            parallelism.get("count") == 2
            and policies == {"experiment_only", "proposal_only"}
            and metrics["recommended_worker_count"] == 3
        )
        return {
            "passed": passed,
            "reward": 1 if passed else 0,
            "metrics": metrics,
            "evidence": {
                "auto_sidecars": int(parallelism.get("count") or 0),
                "write_policies": sorted(policies),
            },
        }

    def natural_fanout(store, runtime):
        fanout = store.task_fanout(
            items=[{"shard": i} for i in range(4)],
            owner="implementer",
            title_prefix="Shard",
            group_id="fanout-4",
        )
        parallelism = runtime.ensure_parallelism(
            max_sidecars_per_primary=2,
            max_new_sidecars=4,
        )
        plan = runtime.coordination_plan(
            active_roles=[],
            max_instances_per_role=4,
            max_total_workers=6,
        )
        metrics = _plan_metrics(plan)
        desired = metrics["recommended_instances"]
        passed = (
            len(fanout["task_ids"]) == 4
            and parallelism.get("count") == 0
            and int(desired.get("implementer", 0)) == 4
            and len(metrics["parallel_frontier"]) == 4
        )
        return {
            "passed": passed,
            "reward": 1 if passed else 0,
            "metrics": metrics,
            "evidence": {
                "synthetic_sidecars": int(parallelism.get("count") or 0),
                "shards": len(fanout["task_ids"]),
            },
        }

    def verification_wait(store, runtime):
        store.task_create(
            "Wait for fresh candidate",
            owner="tester",
            task_id="verify-wait",
        )
        store.task_update(
            "verify-wait",
            status="blocked",
            summary="verification_failure: waiting for fresh upstream candidate",
        )
        plan = runtime.coordination_plan(active_roles=[], max_total_workers=6)
        metrics = _plan_metrics(plan)
        node = next(
            item for item in plan["task_nodes"]
            if item.get("task_id") == "verify-wait"
        )
        passed = (
            node.get("route") == "wait_verification"
            and metrics["recommended_worker_count"] == 0
        )
        return {
            "passed": passed,
            "reward": 1 if passed else 0,
            "metrics": metrics,
            "evidence": {"route": node.get("route")},
        }

    def fan_in(store, runtime):
        store.task_create("Left", owner="researcher", task_id="left")
        store.task_create("Right", owner="tester", task_id="right")
        store.task_create(
            "Join",
            owner="reviewer",
            task_id="join",
            dependencies=["left", "right"],
        )
        plan = runtime.coordination_plan(active_roles=[], max_total_workers=6)
        metrics = _plan_metrics(plan)
        join = next(item for item in plan["task_nodes"] if item.get("task_id") == "join")
        passed = (
            join.get("route") == "wait_dependencies"
            and set(metrics["parallel_frontier"]) == {"left", "right"}
        )
        return {
            "passed": passed,
            "reward": 1 if passed else 0,
            "metrics": metrics,
            "evidence": {
                "join_route": join.get("route"),
                "unresolved": list(join.get("unresolved_dependencies") or []),
            },
        }

    def capability_slot_reuse(store, runtime):
        store.task_create(
            "Flexible analysis",
            owner="researcher",
            preferred_role="researcher",
            required_capabilities=["analysis"],
            task_id="flex-analysis",
        )
        plan = runtime.coordination_plan(
            active_roles=["tester"],
            max_total_workers=6,
        )
        metrics = _plan_metrics(plan)
        node = next(
            item for item in plan["task_nodes"]
            if item.get("task_id") == "flex-analysis"
        )
        recommendation = plan.get("scale_recommendation") or {}
        passed = (
            node.get("route") == "dispatch"
            and node.get("assigned_role") == "tester"
            and "researcher" in (node.get("compatible_roles") or [])
            and recommendation.get("add_roles") == []
        )
        return {
            "passed": passed,
            "reward": 1 if passed else 0,
            "metrics": metrics,
            "evidence": {
                "preferred_role": node.get("preferred_role"),
                "assigned_role": node.get("assigned_role"),
                "compatible_roles": list(node.get("compatible_roles") or []),
            },
        }

    def resource_intent_serialization(store, runtime):
        first = store.task_create(
            "Writer A",
            task_id="writer-a",
            required_capabilities=["implementation"],
            write_resources=["src/shared.py"],
            canonical_root=str(store.root),
        )
        second = store.task_create(
            "Writer B",
            task_id="writer-b",
            required_capabilities=["implementation"],
            write_resources=["src/shared.py"],
            canonical_root=str(store.root),
        )
        store.task_claim(first["task_id"], "implementer", lease_seconds=60)
        blocked_plan = runtime.coordination_plan(
            active_roles=["implementer", "implementer"],
            max_total_workers=6,
        )
        blocked_node = next(
            item for item in blocked_plan["task_nodes"]
            if item.get("task_id") == second["task_id"]
        )
        store.task_release(first["task_id"], "implementer", status="done")
        ready_plan = runtime.coordination_plan(
            active_roles=["implementer"],
            max_total_workers=6,
        )
        ready_node = next(
            item for item in ready_plan["task_nodes"]
            if item.get("task_id") == second["task_id"]
        )
        passed = (
            blocked_node.get("route") == "wait_resource"
            and bool(blocked_node.get("resource_conflicts"))
            and ready_node.get("route") == "dispatch"
        )
        return {
            "passed": passed,
            "reward": 1 if passed else 0,
            "metrics": _plan_metrics(ready_plan),
            "evidence": {
                "blocked_route": blocked_node.get("route"),
                "conflict_count": len(blocked_node.get("resource_conflicts") or []),
                "after_release_route": ready_node.get("route"),
            },
        }

    def worker_budget(store, runtime):
        owners = ["implementer", "tester", "reviewer", "researcher", "debugger", "documenter"]
        for index in range(12):
            store.task_create(
                "Budget task {0}".format(index),
                owner=owners[index % len(owners)],
                task_id="budget-{0}".format(index),
            )
        plan = runtime.coordination_plan(
            active_roles=[],
            max_instances_per_role=4,
            max_total_workers=6,
        )
        metrics = _plan_metrics(plan)
        passed = metrics["recommended_worker_count"] <= 6
        return {
            "passed": passed,
            "reward": 1 if passed else 0,
            "metrics": metrics,
            "evidence": {"worker_budget": 6},
        }

    for name, setup in [
        ("single_primary_sidecars", single_primary),
        ("natural_fanout", natural_fanout),
        ("verification_wait_zero_pressure", verification_wait),
        ("fan_in_barrier", fan_in),
        ("capability_slot_reuse", capability_slot_reuse),
        ("resource_intent_serialization", resource_intent_serialization),
        ("global_worker_budget", worker_budget),
    ]:
        episodes.append(_episode(name, setup))

    passed = all(bool(item.get("passed")) for item in episodes)
    result = {
        "ok": passed,
        "study_id": "study_" + uuid.uuid4().hex[:12],
        "recorded_at": time.time(),
        "revision": BENCHMARK_REVISION,
        "was_version": __version__,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "scheduler_source_hash": _scheduler_source_hash(),
        "episode_count": len(episodes),
        "passed_count": sum(1 for item in episodes if item.get("passed")),
        "total_reward": sum(int(item.get("reward") or 0) for item in episodes),
        "episodes": episodes,
    }
    if record:
        result["journal_path"] = _record_benchmark(result, journal_path)
    return result
