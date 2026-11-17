"""Command-line interface."""

import argparse
import json
import platform
import shutil
import sys

from .benchmark import benchmark_history, run_scheduler_benchmark
from .bridge import BridgeClient
from .state import DurableStore
from .swarm import SwarmRuntime
from .watcher import run as watch_run


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parse_instances(values):
    result = {}
    for item in values or []:
        text = str(item or "").strip()
        if "=" not in text:
            raise ValueError("instance count must use role=count")
        role, count = text.split("=", 1)
        result[role.strip().lower()] = int(count)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="web-agent-swarm")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("doctor")
    sub.add_parser("tabs")
    sub.add_parser("snapshot")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--record", action="store_true")
    benchmark.add_argument("--journal", default="")
    benchmark_history_parser = sub.add_parser("benchmark-history")
    benchmark_history_parser.add_argument("--limit", type=int, default=20)
    benchmark_history_parser.add_argument("--journal", default="")

    plan = sub.add_parser("plan")
    plan.add_argument("--workers", type=int, default=0)
    plan.add_argument("--roles", nargs="*")

    start = sub.add_parser("start")
    start.add_argument("--roles", nargs="*", default=[])
    start.add_argument("--instances", nargs="*", default=[])
    start.add_argument("--spawn-url", default="")
    start.add_argument("--no-watch", action="store_true")
    start.add_argument("--interval", type=float, default=300.0)
    start.add_argument("--max-silence", type=float, default=300.0)

    coordination = sub.add_parser("coordination-plan")
    coordination.add_argument("--active-roles", nargs="*")
    coordination.add_argument("--max-new-roles", type=int, default=6)
    coordination.add_argument("--max-instances-per-role", type=int, default=4)
    coordination.add_argument("--max-total-workers", type=int, default=6)

    elastic = sub.add_parser("elastic-plan")
    elastic.add_argument("--max-new-roles", type=int, default=6)
    elastic.add_argument("--max-instances-per-role", type=int, default=4)
    elastic.add_argument("--max-total-workers", type=int, default=6)

    scale = sub.add_parser("scale")
    scale.add_argument("--workers", type=int, default=0)
    scale.add_argument("--roles", nargs="*")
    scale.add_argument("--instances", nargs="*", default=[])
    scale.add_argument("--max-new-roles", type=int, default=6)
    scale.add_argument("--max-instances-per-role", type=int, default=4)
    scale.add_argument("--max-total-workers", type=int, default=6)
    scale.add_argument("--scale-in-grace", type=float, default=300.0)
    scale.add_argument("--spawn-url", default="")
    scale.add_argument("--no-watch", action="store_true")
    scale.add_argument("--interval", type=float, default=300.0)
    scale.add_argument("--max-silence", type=float, default=300.0)

    send = sub.add_parser("send")
    send.add_argument("name")
    send.add_argument("text")

    create = sub.add_parser("task-create")
    create.add_argument("title")
    create.add_argument("--description", default="")
    create.add_argument("--owner", default="")
    create.add_argument("--depends-on", nargs="*", default=[])

    # Direct, on-demand task execution via Remote Desktop Commander requires no
    # browser extension, local model, Codex, API key, or persistent service.
    list_tasks = sub.add_parser("task-list")
    list_tasks.add_argument("--status", choices=["ready", "active", "blocked", "review", "done", "failed"])

    claim = sub.add_parser("task-claim")
    claim.add_argument("task_id")
    claim.add_argument("worker_name")
    claim.add_argument("--lease-seconds", type=int, default=900)

    heartbeat = sub.add_parser("task-heartbeat")
    heartbeat.add_argument("task_id")
    heartbeat.add_argument("worker_name")
    heartbeat.add_argument("--lease-seconds", type=int, default=900)

    release = sub.add_parser("task-release")
    release.add_argument("task_id")
    release.add_argument("worker_name")
    release.add_argument("--status", choices=["ready", "blocked", "review", "done", "failed"], default="done")
    release.add_argument("--summary", default="")
    release.add_argument("--result-json", default=None)
    release.add_argument("--error", default="")
    release.add_argument("--no-retry", action="store_true")

    update = sub.add_parser("task-update")
    update.add_argument("task_id")
    update.add_argument("--status", default="")
    update.add_argument("--owner", default="")
    update.add_argument("--summary", default="")

    status = sub.add_parser("status-set")
    status.add_argument("key")
    status.add_argument("value")

    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=300.0)
    watch.add_argument("--max-silence", type=float, default=300.0)

    args = parser.parse_args(argv)
    bridge = BridgeClient()
    store = DurableStore()
    swarm = SwarmRuntime(bridge=bridge, store=store)

    if args.command == "health":
        emit(bridge.health())
    elif args.command == "doctor":
        bridge_health = bridge.health()
        state_health = store.diagnostics()
        checks = {
            "python": {"ok": sys.version_info >= (3, 10), "version": platform.python_version()},
            "node": {"ok": bool(shutil.which("node")), "path": shutil.which("node") or ""},
            "bridge": bridge_health,
            "durable_state": state_health,
            "bridge_token": {"ok": bool(bridge.token), "configured": bool(bridge.token), "path": str(bridge.token_file)},
            "protocol": {
                "ok": bool(bridge_health.get("protocol_compatible")),
                "bridge_min": int(bridge_health.get("protocolMin") or bridge_health.get("protocolVersion") or 0),
                "bridge_max": int(bridge_health.get("protocolMax") or bridge_health.get("protocolVersion") or 0),
            },
            "extension": {
                "ok": int(bridge_health.get("extensionClients") or 0) == 1,
                "clients": int(bridge_health.get("extensionClients") or 0),
                "versions": list(bridge_health.get("extensionVersions") or []),
            },
        }
        emit({
            "ok": all(bool(item.get("ok")) for item in checks.values()),
            "checks": checks,
            "recovery": [
                "Start the localhost bridge if bridge.ok is false.",
                "Open the extension options and paste the bridge token if bridge_token.ok is false.",
                "Reload/update the extension when protocol.ok is false.",
                "Inspect the durable-state backup/quarantine files when durable_state.ok is false.",
            ],
        })
    elif args.command == "tabs":
        emit(bridge.tabs())
    elif args.command == "snapshot":
        emit(swarm.snapshot())
    elif args.command == "benchmark":
        emit(run_scheduler_benchmark(
            record=args.record,
            journal_path=args.journal,
        ))
    elif args.command == "benchmark-history":
        emit(benchmark_history(
            limit=args.limit,
            journal_path=args.journal,
        ))
    elif args.command == "plan":
        emit(swarm.plan(args.workers, args.roles or None))
    elif args.command == "start":
        emit(swarm.start(
            args.roles or None,
            instances=parse_instances(args.instances) or None,
            spawn_url=args.spawn_url,
            start_watch=not args.no_watch,
            interval=args.interval,
            max_silence=args.max_silence,
        ))
    elif args.command == "coordination-plan":
        emit(swarm.coordination_plan(
            active_roles=args.active_roles,
            max_new_roles=args.max_new_roles,
            max_instances_per_role=args.max_instances_per_role,
            max_total_workers=args.max_total_workers,
        ))
    elif args.command == "elastic-plan":
        emit(swarm.elastic_plan(
            max_new_roles=args.max_new_roles,
            max_instances_per_role=args.max_instances_per_role,
            max_total_workers=args.max_total_workers,
        ))
    elif args.command == "scale":
        emit(swarm.scale(
            requested_roles=args.roles or None,
            requested_instances=parse_instances(args.instances) or None,
            worker_count=args.workers,
            max_new_roles=args.max_new_roles,
            max_instances_per_role=args.max_instances_per_role,
            max_total_workers=args.max_total_workers,
            scale_in_grace_seconds=args.scale_in_grace,
            spawn_url=args.spawn_url,
            start_watch=not args.no_watch,
            interval=args.interval,
            max_silence=args.max_silence,
        ))
    elif args.command == "send":
        emit(swarm.send(args.name, args.text))
    elif args.command == "task-create":
        emit(store.task_create(
            args.title,
            args.description,
            args.owner,
            dependencies=args.depends_on,
        ))
    elif args.command == "task-list":
        tasks = store.tasks()
        emit([task for task in tasks if not args.status or task.get("status") == args.status])
    elif args.command == "task-claim":
        emit(store.task_claim(
            args.task_id, args.worker_name, lease_seconds=args.lease_seconds,
        ))
    elif args.command == "task-heartbeat":
        emit(store.task_heartbeat(
            args.task_id, args.worker_name, lease_seconds=args.lease_seconds,
        ))
    elif args.command == "task-release":
        emit(store.task_release(
            args.task_id, args.worker_name,
            status=args.status,
            summary=args.summary,
            result=json.loads(args.result_json) if args.result_json is not None else None,
            error=args.error,
            retryable=not args.no_retry,
        ))
    elif args.command == "task-update":
        emit(store.task_update(args.task_id, args.status, args.owner, args.summary))
    elif args.command == "status-set":
        emit(store.status_set(args.key, args.value))
    elif args.command == "watch":
        return watch_run(args.interval, args.max_silence, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
