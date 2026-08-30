"""Role profiles and read-only planning."""

from typing import Dict, List, Optional

DEFAULT_ROLES = {
    "researcher": "Investigate the problem, gather evidence, compare approaches, and report concise findings.",
    "implementer": "Make focused implementation changes and keep the shared task state current.",
    "tester": "Design and run verification, reproduce failures, and report concrete pass/fail evidence.",
    "reviewer": "Review changes and claims, identify gaps, and require evidence before completion.",
    "debugger": "Isolate failures, form testable hypotheses, and drive root-cause analysis.",
    "documenter": "Turn validated results into concise reusable documentation and handoff notes.",
}

DEFAULT_PRIORITY = [
    "implementer",
    "tester",
    "reviewer",
    "researcher",
    "debugger",
    "documenter",
]


def plan_roles(
    tasks: List[Dict[str, object]],
    worker_count: int = 0,
    requested_roles: Optional[List[str]] = None,
) -> Dict[str, object]:
    if requested_roles:
        roles = []
        for role in requested_roles:
            clean = str(role or "").strip().lower()
            if clean not in DEFAULT_ROLES:
                return {"ok": False, "error": "unknown role: {0}".format(clean)}
            if clean not in roles:
                roles.append(clean)
        if not roles:
            return {"ok": False, "error": "at least one role is required"}
        return {
            "ok": True,
            "worker_count": len(roles),
            "roles": roles,
            "basis": "user_requested_roles",
        }

    requested = int(worker_count or 0)
    if requested < 0 or requested > len(DEFAULT_ROLES):
        return {
            "ok": False,
            "error": "worker_count must be between 0 and {0}".format(len(DEFAULT_ROLES)),
        }

    scores = {role: len(DEFAULT_PRIORITY) - idx for idx, role in enumerate(DEFAULT_PRIORITY)}
    task_map = {
        str(task.get("task_id") or ""): task
        for task in tasks
        if isinstance(task, dict) and str(task.get("task_id") or "")
    }
    runnable_count = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "").lower()
        dependencies = [str(item) for item in task.get("dependencies", []) if str(item)]
        unresolved = [
            dep for dep in dependencies
            if task_map.get(dep, {}).get("status") != "done"
        ]
        runnable = (
            status == "active"
            or (status in {"ready", "review"} and not unresolved)
        )
        if not runnable:
            continue
        runnable_count += 1
        owner = str(task.get("owner") or "").strip().lower()
        if owner in scores:
            scores[owner] += 100
        if status == "review":
            scores["reviewer"] += 50

    ranked = sorted(DEFAULT_PRIORITY, key=lambda role: (-scores[role], DEFAULT_PRIORITY.index(role)))
    if requested:
        count = requested
        basis = "user_requested_count"
    elif runnable_count:
        count = min(max(1, runnable_count), len(DEFAULT_ROLES))
        basis = "current_runnable_frontier"
    else:
        count = 0
        basis = "main_only_no_runnable_work"

    return {
        "ok": True,
        "worker_count": count,
        "roles": ranked[:count],
        "basis": basis,
    }
