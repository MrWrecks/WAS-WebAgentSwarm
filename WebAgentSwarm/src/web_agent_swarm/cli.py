"""Command-line interface."""

import argparse
import json

from .bridge import BridgeClient
from .state import DurableStore
from .swarm import SwarmRuntime
from .watcher import run as watch_run


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


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
    start.add_argument("--spawn-url", default="")
    start.add_argument("--no-watch", action="store_true")
    start.add_argument("--interval", type=float, default=15.0)
    start.add_argument("--max-silence", type=float, default=300.0)

    send = sub.add_parser("send")
    send.add_argument("name")
    send.add_argument("text")

    create = sub.add_parser("task-create")
    create.add_argument("title")
    create.add_argument("--description", default="")
    create.add_argument("--owner", default="")

    update = sub.add_parser("task-update")
    update.add_argument("task_id")
    update.add_argument("--status", default="")
    update.add_argument("--owner", default="")
    update.add_argument("--summary", default="")

    status = sub.add_parser("status-set")
    status.add_argument("key")
    status.add_argument("value")

    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=15.0)
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
            spawn_url=args.spawn_url,
            start_watch=not args.no_watch,
            interval=args.interval,
            max_silence=args.max_silence,
        ))
    elif args.command == "send":
        emit(swarm.send(args.name, args.text))
    elif args.command == "task-create":
        emit(store.task_create(args.title, args.description, args.owner))
    elif args.command == "task-update":
        emit(store.task_update(args.task_id, args.status, args.owner, args.summary))
    elif args.command == "status-set":
        emit(store.status_set(args.key, args.value))
    elif args.command == "watch":
        return watch_run(args.interval, args.max_silence, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
