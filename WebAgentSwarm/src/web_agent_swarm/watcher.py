"""Event-aware watcher that wakes the main Web Agent on state changes."""

import argparse
import hashlib
import json
import time
from typing import Any

from .swarm import SwarmRuntime


TICK = (
    "[WEB_SWARM_TICK] Shared worker/task state changed. "
    "Call web_agent_swarm_snapshot, inspect durable tasks/status, and dispatch only "
    "to idle workers when a real next action is warranted. Avoid duplicate instructions."
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


def _digest(snapshot: dict) -> str:
    workers = [
        item for item in snapshot.get("agents", [])
        if item.get("name") != "main"
    ]
    payload = {
        "workers": workers,
        "tasks": snapshot.get("tasks", []),
        "shared_status": snapshot.get("shared_status", {}),
    }
    raw = json.dumps(
        _stable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _main_idle(snapshot: dict) -> bool:
    for item in snapshot.get("agents", []):
        if item.get("name") != "main":
            continue
        ui = item.get("ui") or {}
        return bool(item.get("open")) and not bool(ui.get("generating"))
    return False


def run(interval: float = 15.0, max_silence: float = 300.0, once: bool = False) -> int:
    runtime = SwarmRuntime()
    last_digest = None
    last_sent = 0.0
    pending = True

    while True:
        snapshot = runtime.snapshot(tail=2)
        digest = _digest(snapshot)
        if last_digest is None or digest != last_digest:
            pending = True

        silence_due = max_silence > 0 and time.time() - last_sent >= max_silence
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
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--max-silence", type=float, default=300.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    return run(args.interval, args.max_silence, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
