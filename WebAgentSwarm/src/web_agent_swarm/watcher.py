"""Event-aware watcher that wakes the main Web Agent on state changes."""

import argparse
import hashlib
import json
import time
from typing import Any

from .swarm import SwarmRuntime
from .watchdogs import (
    BrowserLifecycleWatchdog,
    collect_snapshot_alerts,
    conversation_limit_reached,
)


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
    "duplicate a valid lease. A send ACK timeout may have executed: reconcile command_id with "
    "the target tab's lastUserText/lastRole/generating state before any resend. Send only a real "
    "durable next action."
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
    return collect_snapshot_alerts(snapshot)


def _digest(snapshot: dict) -> str:
    payload = {
        "tasks": snapshot.get("tasks", []),
        "task_steps": snapshot.get("task_steps", []),
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


def _watch_snapshot(runtime: SwarmRuntime) -> dict:
    """Build a watcher snapshot without inspecting every worker DOM.

    The watcher only needs durable coordination state plus a writable main Web
    turn. Worker DOM inspection belongs to the main Agent after it receives the
    tick. This prevents one frozen/unresponsive worker from serially blocking
    the heartbeat loop.
    """
    bindings = runtime.store.bindings()
    agents = []
    main_binding = bindings.get("main") if isinstance(bindings, dict) else None
    main_tab = runtime._resolve("main")
    main_item = {
        "name": "main",
        "binding": main_binding,
        "open": bool(main_tab),
        "tab": main_tab,
        "ui": None,
        "error": None,
        "browser_state": (
            "discarded"
            if bool((main_tab or {}).get("discarded"))
            else (
                "frozen"
                if bool((main_tab or {}).get("frozen"))
                else ("active" if main_tab else "closed")
            )
        ),
    }
    if main_tab:
        inspected = runtime.bridge.inspect(int(main_tab["id"]), tail=2)
        if inspected.get("ok"):
            main_item["ui"] = inspected.get("snapshot")
            inspected_tab = inspected.get("tab")
            if isinstance(inspected_tab, dict):
                main_item["tab"] = inspected_tab
                main_item["browser_state"] = (
                    "discarded"
                    if bool(inspected_tab.get("discarded"))
                    else (
                        "frozen"
                        if bool(inspected_tab.get("frozen"))
                        else "active"
                    )
                )
        else:
            main_item["error"] = inspected.get("error")
            hint = str(inspected.get("browserState") or "").strip()
            if hint in {"discarded", "frozen", "unresponsive"}:
                main_item["browser_state"] = hint
            elif "Timed out waiting for extension response" in str(
                inspected.get("error") or ""
            ):
                main_item["browser_state"] = "unresponsive"
    else:
        main_item["error"] = "bound conversation is not currently open"
    agents.append(main_item)

    raw_tasks = runtime.store.tasks()
    now = time.time()
    active_owners = {
        str(task.get("claimed_by") or "")
        for task in raw_tasks
        if isinstance(task, dict)
        and str(task.get("status") or "").lower() == "active"
        and str(task.get("claimed_by") or "")
        and float(task.get("lease_expires_at") or 0.0) > now
    }
    for name in sorted(bindings):
        if name == "main":
            continue
        binding = bindings[name]
        worker = {
            "name": name,
            "binding": binding,
            "open": None,
            "tab": None,
            "ui": None,
            "error": None,
            "browser_state": "unknown",
        }
        if name in active_owners and not bool((binding or {}).get("suspended")):
            tab = runtime._resolve(name)
            worker["open"] = bool(tab)
            worker["tab"] = tab
            if tab:
                inspected = runtime.bridge.inspect(int(tab["id"]), tail=1)
                if inspected.get("ok"):
                    worker["ui"] = inspected.get("snapshot")
                    worker["browser_state"] = "active"
                else:
                    worker["error"] = inspected.get("error")
                    hint = str(inspected.get("browserState") or "").strip()
                    worker["browser_state"] = (
                        hint if hint in {"discarded", "frozen", "unresponsive"}
                        else (
                            "unresponsive"
                            if "Timed out waiting for extension response" in str(inspected.get("error") or "")
                            else "active"
                        )
                    )
            else:
                worker["browser_state"] = "closed"
                worker["error"] = "bound conversation is not currently open"
        agents.append(worker)

    coordination = runtime.coordination_plan()
    kind_by_task = {
        str(node.get("task_id") or ""): str(node.get("blocker_kind") or "")
        for node in coordination.get("task_nodes", [])
        if isinstance(node, dict)
    }
    route_by_task = {
        str(node.get("task_id") or ""): str(node.get("route") or "")
        for node in coordination.get("task_nodes", [])
        if isinstance(node, dict)
    }
    tasks = []
    for task in raw_tasks:
        item = dict(task)
        task_id = str(item.get("task_id") or "")
        if str(item.get("status") or "").lower() == "blocked":
            item["blocker_kind"] = (
                kind_by_task.get(task_id)
                or runtime._blocker_kind(item)
            )
        item["route"] = route_by_task.get(task_id) or "terminal"
        tasks.append(item)

    durable = runtime.store.load()
    return {
        "ok": True,
        "agents": agents,
        "tasks": tasks,
        "task_steps": [
            dict(record)
            for steps in durable.get("task_steps", {}).values()
            if isinstance(steps, dict)
            for record in steps.values()
            if isinstance(record, dict)
        ],
        "shared_status": runtime.store.status(),
        "supersteps": runtime.store.supersteps(include_pending=False),
        "interrupts": runtime.store.interrupts(),
        "active_graph": runtime.store.graph_get(),
        "lanes": list(durable.get("lanes", {}).values()),
        "resource_claims": list(durable.get("resource_claims", {}).values()),
        "commits": list(durable.get("commits", {}).values()),
        "canonical_epoch": int(durable.get("canonical_epoch") or 0),
    }


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
            if kind not in {
                "dependency_wait",
                "external_wait",
                "verification_failure",
            }:
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
        if isinstance(binding, dict) and binding.get("suspended"):
            continue
        if str(agent.get("browser_state") or "") in {
            "discarded",
            "frozen",
            "unresponsive",
        }:
            return True
        if (
            isinstance(binding, dict)
            and float(binding.get("excess_since") or 0.0) > 0.0
        ):
            return True
    return False


def _scale_out_productive_work(runtime: SwarmRuntime) -> dict:
    """Apply only durable-pressure scale-out; never remove workers here.

    This lets natural fan-out and safe JIT sidecars become real browser workers
    without waiting for the main Agent to perform a second scheduling turn.
    Scale-in remains on the guarded orchestrator path so a stuck worker UI cannot
    block this lightweight watcher loop.
    """
    elastic = runtime.elastic_plan(
        max_new_roles=4,
        max_instances_per_role=4,
        max_total_workers=6,
    )
    if not elastic.get("ok"):
        return elastic
    additions = [
        item
        for item in elastic.get("add_instances", [])
        if isinstance(item, dict) and int(item.get("count") or 0) > 0
    ]
    if not additions:
        return {"ok": True, "scaled": False, "targets": {}}

    active = elastic.get("active_instances") or {}
    targets = {
        str(item.get("role") or ""): (
            int(active.get(str(item.get("role") or ""), 0))
            + int(item.get("count") or 0)
        )
        for item in additions
        if str(item.get("role") or "")
    }
    if not targets:
        return {"ok": True, "scaled": False, "targets": {}}
    result = runtime.scale(
        requested_instances=targets,
        start_watch=False,
    )
    result["targets"] = targets
    return result


def _browser_event_requires_tick(event: dict) -> bool:
    return BrowserLifecycleWatchdog.requires_tick(event)


def _main_idle(snapshot: dict) -> bool:
    for item in snapshot.get("agents", []):
        if item.get("name") != "main":
            continue
        if str(item.get("browser_state") or "") in {
            "discarded",
            "frozen",
            "unresponsive",
        }:
            return False
        ui = item.get("ui") or {}
        if item.get("error") and not ui:
            return False
        return (
            bool(item.get("open"))
            and not bool(ui.get("generating"))
            and bool(ui.get("composerPresent", True))
            and not conversation_limit_reached(ui)
        )
    return False


def run(interval: float = 300.0, max_silence: float = 300.0, once: bool = False) -> int:
    runtime = SwarmRuntime()
    last_digest = None
    last_sent = 0.0
    pending = True
    browser_event_seq = 0

    while True:
        # Keep productive parallelism work-conserving without inspecting worker
        # DOMs: materialize only bounded safe sidecars from durable primary work.
        runtime.ensure_parallelism(
            max_sidecars_per_primary=2,
            max_new_sidecars=4,
        )
        _scale_out_productive_work(runtime)
        snapshot = _watch_snapshot(runtime)

        main_item = next(
            (
                item
                for item in snapshot.get("agents", [])
                if isinstance(item, dict) and item.get("name") == "main"
            ),
            {},
        )
        if conversation_limit_reached(main_item.get("ui")):
            rollover = runtime.rollover_main_if_exhausted()
            if rollover.get("rolled_over"):
                snapshot = _watch_snapshot(runtime)
                pending = True
                last_digest = None
            elif once:
                return 3
            else:
                time.sleep(max(2.0, float(interval)))
                continue

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

        wait_ms = int(max(2.0, float(interval)) * 1000)
        event_result = runtime.bridge.events(
            since=browser_event_seq,
            limit=64,
            wait_ms=min(wait_ms, 30000),
        )
        if event_result.get("ok"):
            events = [
                event
                for event in event_result.get("events", [])
                if isinstance(event, dict)
            ]
            if events:
                browser_event_seq = max(
                    browser_event_seq,
                    max(int(event.get("seq") or 0) for event in events),
                )
                if any(_browser_event_requires_tick(event) for event in events):
                    pending = True
            elif wait_ms > 30000:
                time.sleep((wait_ms - 30000) / 1000.0)
        else:
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
