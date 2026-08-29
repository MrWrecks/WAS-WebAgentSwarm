"""Command-line interface."""

import argparse
import json

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
    sub.add_parser("tabs")
    sub.add_parser("snapshot")

    plan = sub.add_parser("plan")
    plan.add_argument("--workers", type=int, default=0)
    plan.add_argument("--roles", nargs="*")

    start = sub.add_parser("start")
    start.add_argument("--roles", nargs="+", required=True)
    start.add_argument("--instances", nargs="*", default=[])
    start.add_argument("--spawn-url", default="")
    start.add_argument("--no-watch", action="store_true")
    start.add_argument("--interval", type=float, default=300.0)
    start.add_argument("--max-silence", type=float, default=300.0)

    coordination = sub.add_parser("coordination-plan")
    coordination.add_argument("--active-roles", nargs="*")
    coordination.add_argument("--max-new-roles", type=int, default=2)
    coordination.add_argument("--max-instances-per-role", type=int, default=4)

    elastic = sub.add_parser("elastic-plan")
    elastic.add_argument("--max-new-roles", type=int, default=2)
    elastic.add_argument("--max-instances-per-role", type=int, default=4)

    scale = sub.add_parser("scale")
    scale.add_argument("--workers", type=int, default=0)
    scale.add_argument("--roles", nargs="*")
    scale.add_argument("--instances", nargs="*", default=[])
    scale.add_argument("--max-new-roles", type=int, default=2)
    scale.add_argument("--max-instances-per-role", type=int, default=4)
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
    elif args.command == "tabs":
        emit(bridge.tabs())
    elif args.command == "snapshot":
        emit(swarm.snapshot())
    elif args.command == "plan":
        emit(swarm.plan(args.workers, args.roles or None))
    elif args.command == "start":
        emit(swarm.start(
            args.roles,
            instances=parse_instances(args.instances),
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
        ))
    elif args.command == "elastic-plan":
        emit(swarm.elastic_plan(
            max_new_roles=args.max_new_roles,
            max_instances_per_role=args.max_instances_per_role,
        ))
    elif args.command == "scale":
        emit(swarm.scale(
            requested_roles=args.roles or None,
            requested_instances=parse_instances(args.instances),
            worker_count=args.workers,
            max_new_roles=args.max_new_roles,
            max_instances_per_role=args.max_instances_per_role,
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
    elif args.command == "task-update":
        emit(store.task_update(args.task_id, args.status, args.owner, args.summary))
    elif args.command == "status-set":
        emit(store.status_set(args.key, args.value))
    elif args.command == "watch":
        return watch_run(args.interval, args.max_silence, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
