"""Event-aware watcher that wakes the main Web Agent on state changes."""

import argparse
import hashlib
import json
import time
from typing import Any

from .swarm import SwarmRuntime


TICK = (
    "[WEB_SWARM_TICK] Shared durable state changed. "
    "Call web_agent_swarm_snapshot and web_agent_swarm_coordination_plan. If an active "
    "SwarmGraph exists, obey its explicit wait_graph/graph_unselected routes and use revision-safe "
    "swarm_graph_* mutations. For repository-changing tasks, require an MVCC lane: claim hierarchical "
    "resources, mutate only the returned lane workspace, heartbeat long lanes, and call swarm_lane_commit "
    "before releasing the task. Treat commit conflict as a durable branch/fork event; never overwrite canonical. "
    "Treat task state as graph routing: respect dependencies, retry waits, active leases, durable human "
    "interrupts, and blocked work. Resume open human-input waits through swarm_interrupt_resume "
    "only when input exists; do not infer it from chat memory. For divisible work, use Send-style "
    "swarm_task_fanout shards and swarm_task_reduce. If an open/ready superstep exists, never "
    "publish staged writes early; commit only when ready_to_commit. Call "
    "web_agent_swarm_elastic_plan when durable pressure changes and apply web_agent_swarm_scale "
    "under the global worker budget. Scale-out follows the runnable parallel frontier; automatic "
    "scale-in uses hysteresis and must respect task leases, supersteps, and generating tabs. "
    "A pending excess_since worker is capacity maintenance work and may need a later scale tick. "
    "Use swarm_checkpoint_create before risky coordination rewrites; restore only when no live "
    "lease or open superstep exists. Never scale merely because a browser tab is idle, never "
    "duplicate a valid lease, and send only a real durable next action."
)


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable(item)
            for key, item in sorted(value.items())
            if key not in {"updated_at"}
        }
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def _transport_alerts(snapshot: dict):
    alerts = []
    for item in snapshot.get("agents", []):
        if item.get("name") == "main":
            continue
        binding = item.get("binding") or {}
        if isinstance(binding, dict) and binding.get("suspended"):
            continue
        ui = item.get("ui") or {}
        error = str(item.get("error") or "").strip()
        if error:
            alerts.append({
                "name": item.get("name"),
                "kind": "inspect_error",
                "error": error,
            })
        if ui.get("stuckGenerating"):
            alerts.append({
                "name": item.get("name"),
                "kind": "stuck_generating",
            })
    return alerts


def _digest(snapshot: dict) -> str:
    payload = {
        "tasks": snapshot.get("tasks", []),
        "shared_status": snapshot.get("shared_status", {}),
        "supersteps": snapshot.get("supersteps", []),
        "interrupts": snapshot.get("interrupts", []),
        "active_graph": snapshot.get("active_graph", {}),
        "lanes": snapshot.get("lanes", []),
        "resource_claims": snapshot.get("resource_claims", []),
        "commits": snapshot.get("commits", []),
        "canonical_epoch": snapshot.get("canonical_epoch", 0),
        "transport_alerts": _transport_alerts(snapshot),
    }
    raw = json.dumps(
        _stable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _has_unresolved_work(snapshot: dict) -> bool:
    now = time.time()
    for task in snapshot.get("tasks", []):
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "").strip().lower()
        route = str(task.get("route") or "").strip().lower()
        if route in {"graph_unselected", "terminal"}:
            continue
        if status in {"ready", "review"}:
            retry_after = float(task.get("retry_after") or 0.0)
            if retry_after <= now:
                return True
        if status == "active":
            return True
        if status == "blocked":
            kind = str(task.get("blocker_kind") or "unknown_blocker")
            if kind not in {"dependency_wait", "external_wait"}:
                return True

    for step in snapshot.get("supersteps", []):
        if isinstance(step, dict) and step.get("status") in {"open", "ready"}:
            return True

    for interrupt in snapshot.get("interrupts", []):
        if isinstance(interrupt, dict) and interrupt.get("status") == "open":
            return True

    for lane in snapshot.get("lanes", []):
        if not isinstance(lane, dict):
            continue
        if lane.get("status") in {
            "creating",
            "active",
            "committing",
            "conflict",
            "recovery_required",
        }:
            return True

    for commit in snapshot.get("commits", []):
        if isinstance(commit, dict) and commit.get("status") in {
            "conflict",
            "failed",
            "recovery_required",
        }:
            return True

    for agent in snapshot.get("agents", []):
        if not isinstance(agent, dict) or agent.get("name") == "main":
            continue
        binding = agent.get("binding") or {}
        if (
            isinstance(binding, dict)
            and not binding.get("suspended")
            and float(binding.get("excess_since") or 0.0) > 0.0
        ):
            return True
    return False


def _main_idle(snapshot: dict) -> bool:
    for item in snapshot.get("agents", []):
        if item.get("name") != "main":
            continue
        ui = item.get("ui") or {}
        return bool(item.get("open")) and not bool(ui.get("generating"))
    return False


def run(interval: float = 300.0, max_silence: float = 300.0, once: bool = False) -> int:
    runtime = SwarmRuntime()
    last_digest = None
    last_sent = 0.0
    pending = True

    while True:
        snapshot = runtime.snapshot(tail=2)
        digest = _digest(snapshot)
        if last_digest is None or digest != last_digest:
            pending = True

        silence_due = (
            max_silence > 0
            and _has_unresolved_work(snapshot)
            and time.time() - last_sent >= max_silence
        )
        if (pending or silence_due) and _main_idle(snapshot):
            result = runtime.send("main", TICK)
            if result.get("ok"):
                last_sent = time.time()
                last_digest = digest
                pending = False
            elif once:
                return 2

        if once:
            return 0

        if last_digest is None:
            last_digest = digest
        time.sleep(max(2.0, float(interval)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--max-silence", type=float, default=300.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    return run(args.interval, args.max_silence, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
