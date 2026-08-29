"""Browser-hosted swarm runtime."""

import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional

from .bridge import BridgeClient
from .roles import DEFAULT_PRIORITY, DEFAULT_ROLES, plan_roles
from .state import DurableStore


_CONVERSATION_RE = re.compile(r"/c/([^/?#]+)")


def conversation_id(url: str) -> str:
    match = _CONVERSATION_RE.search(str(url or ""))
    if not match:
        return ""
    cid = match.group(1)
    if ":" in cid:
        return ""
    return cid


class SwarmRuntime:
    def __init__(
        self,
        bridge: Optional[BridgeClient] = None,
        store: Optional[DurableStore] = None,
    ) -> None:
        self.bridge = bridge or BridgeClient()
        self.store = store or DurableStore()
        self.watcher_pid_path = self.store.root / "watcher.pid"
        self.watcher_log_path = self.store.root / "watcher.log"

    def plan(
        self,
        worker_count: int = 0,
        requested_roles: Optional[List[str]] = None,
    ) -> Dict[str, object]:
        requested_total = int(worker_count or 0)
        if requested_total < 0 or requested_total > 32:
            return {"ok": False, "error": "worker_count must be between 0 and 32"}
        base_count = min(requested_total, len(DEFAULT_ROLES)) if requested_total else 0
        result = plan_roles(
            self.store.tasks(),
            worker_count=base_count,
            requested_roles=requested_roles,
        )
        if not result.get("ok"):
            return result
        roles = list(result["roles"])
        coordination = self.coordination_plan(
            active_roles=[],
            max_new_roles=16,
            max_instances_per_role=4,
        )
        instance_plan = [
            {"role": role, "count": 1}
            for role in roles
        ]

        if requested_total > len(instance_plan) and instance_plan:
            counts = {item["role"]: int(item["count"]) for item in instance_plan}
            if requested_roles:
                candidates = list(roles)
            else:
                pressure_roles = [
                    str(item.get("role") or "")
                    for item in coordination.get("role_pressure", [])
                    if int(item.get("score") or 0) > 0
                ]
                candidates = []
                for role in pressure_roles + roles + list(DEFAULT_PRIORITY):
                    if role in DEFAULT_ROLES and role not in candidates:
                        candidates.append(role)
            remaining = requested_total - sum(counts.values())
            cursor = 0
            while remaining > 0 and candidates:
                role = candidates[cursor % len(candidates)]
                if counts.get(role, 0) < 16:
                    counts[role] = counts.get(role, 0) + 1
                    remaining -= 1
                cursor += 1
                if cursor > 1024:
                    break
            roles = [role for role in candidates if counts.get(role, 0) > 0]
            instance_plan = [
                {"role": role, "count": counts[role]}
                for role in roles
                if counts.get(role, 0) > 0
            ]
            result["basis"] = "user_requested_count_instances"

        if (
            not requested_roles
            and not requested_total
            and coordination.get("ok")
        ):
            graph_instances = list(
                (coordination.get("scale_recommendation") or {}).get(
                    "add_instances"
                )
                or []
            )
            if graph_instances:
                instance_plan = [
                    {
                        "role": str(item["role"]),
                        "count": max(1, int(item["count"])),
                    }
                    for item in graph_instances
                ]
                roles = [item["role"] for item in instance_plan]
                result["basis"] = "durable_task_graph_instances"

        total_workers = sum(int(item["count"]) for item in instance_plan)
        role_text = ", ".join(
            "{0}x{1}".format(item["role"], item["count"])
            if int(item["count"]) > 1
            else str(item["role"])
            for item in instance_plan
        )
        result.update({
            "phase": "proposal",
            "requires_confirmation": True,
            "roles": roles,
            "worker_count": total_workers,
            "total_web_agents": total_workers + 1,
            "includes_main": True,
            "instance_plan": instance_plan,
            "role_plan": [
                {
                    "role": item["role"],
                    "instances": int(item["count"]),
                    "responsibility": DEFAULT_ROLES[item["role"]],
                }
                for item in instance_plan
            ],
            "confirmation_prompt": (
                "Proposed swarm: {0} worker(s) + 1 main agent: {1}. "
                "Confirm, change the total, roles, or per-role instance counts."
            ).format(total_workers, role_text),
            "coordination": coordination,
        })
        return result

    @staticmethod
    def _task_role(task: Dict[str, object]) -> str:
        status = str(task.get("status") or "").strip().lower()
        if status == "review":
            return "reviewer"
        if status == "blocked":
            return "debugger"
        owner = str(task.get("owner") or "").strip().lower()
        if owner in DEFAULT_ROLES:
            return owner
        return "implementer"

    def _bound_worker_instances(self) -> Dict[str, List[str]]:
        instances = {role: [] for role in DEFAULT_ROLES}
        for name, binding in self.store.bindings().items():
            if name == "main" or not isinstance(binding, dict):
                continue
            role = str(binding.get("role") or "").strip().lower()
            if role in DEFAULT_ROLES:
                instances[role].append(str(name))
        for role in instances:
            instances[role].sort()
        return instances

    def _bound_worker_roles(self) -> List[str]:
        instances = self._bound_worker_instances()
        return [role for role in DEFAULT_PRIORITY if instances.get(role)]

    @staticmethod
    def _worker_instance_name(role: str, index: int) -> str:
        return role if int(index) <= 1 else "{0}-{1}".format(role, int(index))

    @staticmethod
    def _next_worker_name(role: str, bindings: Dict[str, object]) -> str:
        if role not in bindings:
            return role
        index = 2
        while "{0}-{1}".format(role, index) in bindings:
            index += 1
        return "{0}-{1}".format(role, index)

    def coordination_plan(
        self,
        active_roles: Optional[List[str]] = None,
        max_new_roles: int = 2,
        max_instances_per_role: int = 4,
    ) -> Dict[str, object]:
        """Derive task routing plus same-role instance pressure from durable state."""
        if active_roles is None:
            bound_instances = self._bound_worker_instances()
            active_instances = {
                role: len(bound_instances.get(role, []))
                for role in DEFAULT_ROLES
            }
        else:
            active_instances = {role: 0 for role in DEFAULT_ROLES}
            for role in active_roles:
                clean = str(role or "").strip().lower()
                if clean not in DEFAULT_ROLES:
                    return {"ok": False, "error": "unknown role: {0}".format(clean)}
                active_instances[clean] += 1

        normalized_active = [
            role for role in DEFAULT_PRIORITY if active_instances.get(role, 0) > 0
        ]
        add_limit = max(0, min(int(max_new_roles or 0), 32))
        per_role_limit = max(1, min(int(max_instances_per_role or 1), 16))
        now = time.time()

        tasks = self.store.tasks()
        task_map = {
            str(task.get("task_id") or ""): task
            for task in tasks
            if isinstance(task, dict) and str(task.get("task_id") or "")
        }
        nodes = []
        edges = []
        frontier = []
        barriers = []
        interrupts = []
        pressure = {
            role: {
                "role": role,
                "responsibility": DEFAULT_ROLES[role],
                "score": 0,
                "claimable_task_ids": [],
                "leased_task_ids": [],
                "interrupt_task_ids": [],
                "reasons": [],
            }
            for role in DEFAULT_ROLES
        }

        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("task_id") or "")
            status = str(task.get("status") or "").strip().lower()
            role = self._task_role(task)
            dependencies = [
                str(item)
                for item in task.get("dependencies", [])
                if str(item)
            ]
            unresolved = [
                dep
                for dep in dependencies
                if task_map.get(dep, {}).get("status") != "done"
            ]
            for dep in dependencies:
                edges.append({"from": dep, "to": task_id, "kind": "dependency"})

            claimed_by = str(task.get("claimed_by") or "")
            lease_expires = float(task.get("lease_expires_at") or 0.0)
            retry_after = float(task.get("retry_after") or 0.0)
            lease_valid = status == "active" and bool(claimed_by) and lease_expires > now
            lease_expired = status == "active" and not lease_valid
            retry_wait = status == "ready" and retry_after > now
            route = "terminal"
            executable = False

            if status == "blocked":
                route = "interrupt"
                interrupts.append({
                    "kind": "blocked_task",
                    "task_id": task_id,
                    "role": role,
                    "summary": str(task.get("summary") or ""),
                })
                pressure[role]["score"] += 40
                pressure[role]["interrupt_task_ids"].append(task_id)
                pressure[role]["reasons"].append("blocked task {0}".format(task_id))
            elif status == "ready" and unresolved:
                route = "wait_dependencies"
            elif retry_wait:
                route = "wait_retry"
            elif lease_valid:
                route = "leased"
                pressure[role]["score"] += 80
                pressure[role]["leased_task_ids"].append(task_id)
                pressure[role]["reasons"].append(
                    "leased task {0} by {1}".format(task_id, claimed_by)
                )
            elif status in {"ready", "review"} or lease_expired:
                route = "dispatch"
                executable = True
                frontier.append({
                    "task_id": task_id,
                    "role": role,
                    "status": status,
                    "payload": task.get("payload"),
                    "group_id": str(task.get("group_id") or ""),
                    "recovery": bool(lease_expired),
                })
                pressure[role]["score"] += 60 if status == "review" else 50
                if lease_expired:
                    pressure[role]["score"] += 20
                pressure[role]["claimable_task_ids"].append(task_id)
                pressure[role]["reasons"].append(
                    "{0} task {1}".format(
                        "expired-lease" if lease_expired else status,
                        task_id,
                    )
                )

            if dependencies:
                barriers.append({
                    "task_id": task_id,
                    "role": role,
                    "dependencies": dependencies,
                    "unresolved": unresolved,
                    "ready": not unresolved,
                })

            nodes.append({
                "task_id": task_id,
                "role": role,
                "owner": str(task.get("owner") or ""),
                "status": status,
                "route": route,
                "executable": executable,
                "dependencies": dependencies,
                "unresolved_dependencies": unresolved,
                "claimed_by": claimed_by,
                "lease_expires_at": lease_expires,
                "attempts": int(task.get("attempts") or 0),
                "max_attempts": int(task.get("max_attempts") or 0),
                "group_id": str(task.get("group_id") or ""),
            })

        priority_index = {
            role: index
            for index, role in enumerate(DEFAULT_PRIORITY)
        }
        for role, row in pressure.items():
            work_units = (
                len(row["claimable_task_ids"])
                + len(row["leased_task_ids"])
                + len(row["interrupt_task_ids"])
            )
            row["current_instances"] = int(active_instances.get(role, 0))
            row["desired_instances"] = min(per_role_limit, work_units) if work_units else 0
            row["missing_instances"] = max(
                0,
                int(row["desired_instances"]) - int(row["current_instances"]),
            )

        ranked = sorted(
            pressure.values(),
            key=lambda item: (
                -int(item["missing_instances"]),
                -int(item["score"]),
                priority_index.get(str(item["role"]), len(priority_index)),
            ),
        )

        remaining = add_limit
        add_instances = []
        for item in ranked:
            if remaining <= 0:
                break
            needed = min(int(item["missing_instances"]), remaining)
            if needed <= 0:
                continue
            add_instances.append({
                "role": str(item["role"]),
                "count": needed,
                "current_instances": int(item["current_instances"]),
                "desired_instances": int(item["desired_instances"]),
            })
            remaining -= needed

        add_roles = [
            item["role"]
            for item in add_instances
            if int(item["current_instances"]) == 0
        ]
        recommended_instances = dict(active_instances)
        for item in add_instances:
            recommended_instances[item["role"]] = (
                int(recommended_instances.get(item["role"], 0)) + int(item["count"])
            )

        return {
            "ok": True,
            "model": "durable_task_graph",
            "graph_semantics": {
                "conditional_edge": (
                    "task state selects dispatch/wait_dependencies/wait_retry/"
                    "leased/interrupt/terminal"
                ),
                "send_map": (
                    "fan-out shards can target multiple instances of the same role "
                    "with different payload state"
                ),
                "reducer": "group results are aggregated after shard completion",
                "fan_in": "dependent tasks wait until every dependency is done",
                "interrupt": "blocked tasks are surfaced to the main orchestrator",
                "lease": "claim/heartbeat prevents duplicate shard execution",
                "retry": "retryable failures return to ready until max_attempts",
            },
            "active_roles": normalized_active,
            "active_instances": active_instances,
            "task_nodes": nodes,
            "dependency_edges": edges,
            "parallel_frontier": frontier,
            "fan_in_barriers": barriers,
            "interrupts": interrupts,
            "role_pressure": ranked,
            "scale_recommendation": {
                "needs_scale": bool(add_instances),
                "add_roles": add_roles,
                "add_instances": add_instances,
                "max_new_workers": add_limit,
                "max_instances_per_role": per_role_limit,
                "recommended_instances": recommended_instances,
                "basis": "claimable + leased + interrupt work units by role",
            },
        }

    def elastic_plan(
        self,
        max_new_roles: int = 2,
        max_instances_per_role: int = 4,
    ) -> Dict[str, object]:
        """Recommend missing-role and same-role runtime instances without browser mutation."""
        coordination = self.coordination_plan(
            active_roles=None,
            max_new_roles=max_new_roles,
            max_instances_per_role=max_instances_per_role,
        )
        if not coordination.get("ok"):
            return coordination
        recommendation = coordination.get("scale_recommendation") or {}
        return {
            "ok": True,
            "phase": "elastic_plan",
            "active_roles": list(coordination.get("active_roles") or []),
            "active_instances": dict(coordination.get("active_instances") or {}),
            "needs_scale": bool(recommendation.get("needs_scale")),
            "add_roles": list(recommendation.get("add_roles") or []),
            "add_instances": list(recommendation.get("add_instances") or []),
            "coordination": coordination,
            "policy": (
                "Scale from durable runnable-shard pressure and leases; multiple workers may "
                "share one role. Browser idle alone is never a scale signal."
            ),
        }

    def _tabs(self) -> List[Dict[str, object]]:
        result = self.bridge.tabs()
        if not result.get("ok"):
            return []
        return [
            item for item in result.get("tabs", [])
            if isinstance(item, dict)
            and str(item.get("url") or "").startswith("https://chatgpt.com/")
        ]

    def _resolve(self, name: str) -> Optional[Dict[str, object]]:
        binding = self.store.bindings().get(name)
        if not isinstance(binding, dict):
            return None
        cid = str(binding.get("conversation_id") or "")
        if not cid:
            return None
        matches = [
            tab for tab in self._tabs()
            if conversation_id(str(tab.get("url") or "")) == cid
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda tab: (
                not bool(tab.get("active")),
                int(tab.get("windowId", 0)),
                int(tab.get("index", 0)),
            )
        )
        return matches[0]

    def _bind(self, name: str, tab: Dict[str, object], kind: str, role: str = "") -> None:
        url = str(tab.get("url") or "")
        cid = conversation_id(url)
        if not cid:
            raise RuntimeError("conversation URL is not stable yet")
        self.store.bind(name, {
            "kind": kind,
            "role": role,
            "conversation_id": cid,
            "url_at_bind": url,
            "title_at_bind": str(tab.get("title") or ""),
        })

    @staticmethod
    def _spawn_url(tabs: List[Dict[str, object]]) -> str:
        for tab in tabs:
            url = str(tab.get("url") or "")
            if url.startswith("https://chatgpt.com/g/"):
                return url.split("/c/", 1)[0] if "/c/" in url else url
        return "https://chatgpt.com/"

    @staticmethod
    def _worker_prompt(role: str, worker_name: str = "") -> str:
        name = str(worker_name or role)
        return (
            "You are Web Agent worker instance {0}. Role: {1}. Responsibility: {2} "
            "Use durable task state, not chat memory, as coordination truth. Before doing a "
            "concrete task, claim it with swarm_task_claim(task_id, worker_name=\"{0}\"); "
            "heartbeat long work and normally release it with result/evidence when done. If "
            "the main agent assigns a superstep_id, claim the task with that superstep_id and "
            "do NOT release it directly: keep the lease alive and stage task_release/status_set "
            "operations with swarm_superstep_stage; "
            "on failure call swarm_superstep_fail. The main agent alone commits the ready barrier. "
            "Multiple workers may share your role, so never work an unclaimed task in parallel "
            "with another instance. If a task carries payload/group_id, treat it as one map shard "
            "and return a compact reducer-friendly result. If CodexPro-style local development "
            "tools are connected, use them for concrete workspace work and verification. Start "
            "by reading the coordination plan and claimable task frontier."
        ).format(name, role, DEFAULT_ROLES[role])

    @staticmethod
    def _main_prompt(roles: List[str]) -> str:
        return (
            "You are the main Web Agent orchestrator. Initial active worker roles: {0}. "
            "Read web_agent_swarm_snapshot and web_agent_swarm_coordination_plan. Treat "
            "task status plus dependency truth as graph routing: dispatch independent "
            "parallel_frontier work, respect fan_in_barriers, and treat blocked tasks as "
            "interrupts. For divisible work, use swarm_task_fanout to create Send-style "
            "map shards with payloads; multiple instances of the same role may claim separate "
            "shards concurrently, and swarm_task_reduce aggregates completed shard results. "
            "For one coordinated round whose outputs must be all-or-nothing, call "
            "swarm_superstep_begin with the exact worker instances, tell each worker to stage "
            "pending task_release/status_set writes, and call swarm_superstep_commit only when "
            "the barrier reports ready_to_commit; any worker failure must abort the round. "
            "Workers must claim/lease tasks before concrete work. Use web_agent_swarm_elastic_plan "
            "to compare runnable shard pressure with active instance counts, then "
            "web_agent_swarm_scale when extra same-role or missing-role workers are justified. "
            "Never scale merely because a browser tab is idle. Do not interrupt generating "
            "workers. Send one concise instruction per durable next action and avoid duplicate "
            "instructions. If CodexPro-style local development tools are available, use them "
            "for concrete repository work and verification rather than guessing."
        ).format(", ".join(roles))

    def _send_when_ready(
        self,
        tab_id: int,
        text: str,
        timeout: float = 20.0,
    ) -> Dict[str, object]:
        deadline = time.time() + timeout
        last = {"ok": False, "error": "send timed out"}
        while time.time() < deadline:
            inspected = self.bridge.inspect(tab_id, tail=1)
            if inspected.get("ok"):
                snapshot = inspected.get("snapshot") or {}
                if snapshot.get("generating"):
                    return {"ok": False, "busy": True, "error": "tab is generating"}
            last = self.bridge.send(tab_id, text)
            if last.get("ok"):
                return last
            time.sleep(0.5)
        return last

    def _wait_conversation(self, tab_id: int, timeout: float = 20.0) -> Optional[Dict[str, object]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for tab in self._tabs():
                if int(tab.get("id", -1)) == int(tab_id):
                    if conversation_id(str(tab.get("url") or "")):
                        return tab
            time.sleep(0.5)
        return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False

    def ensure_watcher(
        self,
        interval: float = 15.0,
        max_silence: float = 300.0,
    ) -> Dict[str, object]:
        if self.watcher_pid_path.is_file():
            try:
                pid = int(self.watcher_pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = 0
            if pid and self._pid_alive(pid):
                return {
                    "ok": True,
                    "already_running": True,
                    "pid": pid,
                    "log": str(self.watcher_log_path),
                }

        self.store.root.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "web_agent_swarm.watcher",
            "--interval",
            str(max(2.0, float(interval))),
            "--max-silence",
            str(max(0.0, float(max_silence))),
        ]
        kwargs = {
            "cwd": os.getcwd(),
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True

        with self.watcher_log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=log_handle,
                **kwargs
            )
        self.watcher_pid_path.write_text(str(process.pid) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "already_running": False,
            "pid": process.pid,
            "log": str(self.watcher_log_path),
        }

    def _spawn_worker_instance(
        self,
        role: str,
        target_url: str,
    ) -> Dict[str, object]:
        clean_role = str(role or "").strip().lower()
        if clean_role not in DEFAULT_ROLES:
            return {"ok": False, "error": "unknown role: {0}".format(clean_role)}
        bindings = self.store.bindings()
        name = self._next_worker_name(clean_role, bindings)
        opened = self.bridge.open_tab(target_url, active=False)
        if not opened.get("ok") or not opened.get("tab"):
            return {
                "ok": False,
                "name": name,
                "role": clean_role,
                "action": "create_failed",
                "error": opened.get("error"),
            }
        tab = opened["tab"]
        sent = self._send_when_ready(
            int(tab["id"]),
            self._worker_prompt(clean_role, name),
        )
        if not sent.get("ok"):
            return {
                "ok": False,
                "name": name,
                "role": clean_role,
                "action": "created",
                "error": sent.get("error"),
            }
        stable = self._wait_conversation(int(tab["id"]))
        if not stable:
            return {
                "ok": False,
                "name": name,
                "role": clean_role,
                "action": "created",
                "error": "conversation URL did not stabilize",
            }
        self._bind(name, stable, "worker", clean_role)
        return {
            "ok": True,
            "name": name,
            "role": clean_role,
            "kind": "worker",
            "action": "created",
            "tab": stable,
        }

    def start(
        self,
        roles: List[str],
        instances: Optional[Dict[str, int]] = None,
        spawn_url: str = "",
        start_watch: bool = True,
        interval: float = 15.0,
        max_silence: float = 300.0,
    ) -> Dict[str, object]:
        selected = []
        target_counts = {}
        for role in roles:
            clean = str(role or "").strip().lower()
            if clean not in DEFAULT_ROLES:
                return {"ok": False, "error": "unknown role: {0}".format(clean)}
            if clean not in selected:
                selected.append(clean)
            target_counts[clean] = max(1, int(target_counts.get(clean, 0)))

        for role, count in (instances or {}).items():
            clean = str(role or "").strip().lower()
            if clean not in DEFAULT_ROLES:
                return {"ok": False, "error": "unknown role: {0}".format(clean)}
            target = max(0, min(int(count or 0), 16))
            if target > 0 and clean not in selected:
                selected.append(clean)
            if target > 0:
                target_counts[clean] = target
            else:
                target_counts.pop(clean, None)
                if clean in selected:
                    selected.remove(clean)

        if not selected:
            return {"ok": False, "error": "at least one worker role is required"}

        desired_worker_names = set()
        for role in selected:
            count = max(1, int(target_counts.get(role, 1)))
            target_counts[role] = count
            for index in range(1, count + 1):
                desired_worker_names.add(self._worker_instance_name(role, index))

        current = self.store.bindings()
        for stale in list(current):
            if stale == "main":
                continue
            if stale not in desired_worker_names:
                self.store.unbind(stale)

        tabs = self._tabs()
        target = str(spawn_url or "").strip() or self._spawn_url(tabs)
        if not target.startswith("https://chatgpt.com/"):
            return {"ok": False, "error": "spawn_url must be a chatgpt.com URL"}

        desired = [("main", "main", "")]
        for role in selected:
            for index in range(1, int(target_counts[role]) + 1):
                desired.append(
                    (
                        self._worker_instance_name(role, index),
                        "worker",
                        role,
                    )
                )
        records = []

        for name, kind, role in desired:
            binding = self.store.bindings().get(name)
            tab = self._resolve(name)
            action = "reused" if tab else "created"

            if not tab:
                reopen_url = (
                    str(binding.get("url_at_bind") or "")
                    if isinstance(binding, dict)
                    else ""
                )
                opened = self.bridge.open_tab(reopen_url or target, active=False)
                if not opened.get("ok") or not opened.get("tab"):
                    records.append({
                        "name": name,
                        "ok": False,
                        "action": "reopen_failed" if reopen_url else "create_failed",
                        "error": opened.get("error"),
                    })
                    continue
                tab = opened["tab"]
                action = "reopened" if reopen_url else "created"
                prompt = (
                    self._main_prompt(selected)
                    if kind == "main"
                    else self._worker_prompt(role, name)
                )
                sent = self._send_when_ready(int(tab["id"]), prompt)
                if not sent.get("ok"):
                    records.append({
                        "name": name,
                        "ok": False,
                        "action": action,
                        "error": sent.get("error"),
                    })
                    continue
                stable = self._wait_conversation(int(tab["id"]))
                if not stable:
                    records.append({
                        "name": name,
                        "ok": False,
                        "action": action,
                        "error": "conversation URL did not stabilize",
                    })
                    continue
                tab = stable
                self._bind(name, tab, kind, role)
            else:
                prompt = self._main_prompt(selected) if kind == "main" else (
                    "Continue as worker instance {0} with role {1}. Re-read the durable "
                    "coordination plan. Claim one eligible task using your exact worker name "
                    "before concrete work; heartbeat/release the lease and do not repeat "
                    "completed or currently leased work."
                ).format(name, role)
                sent = self._send_when_ready(int(tab["id"]), prompt, timeout=3.0)
                if not sent.get("ok") and not sent.get("busy"):
                    records.append({
                        "name": name,
                        "ok": False,
                        "action": action,
                        "error": sent.get("error"),
                    })
                    continue

            records.append({
                "name": name,
                "role": role,
                "kind": kind,
                "ok": True,
                "action": action,
                "tab": tab,
            })

        watcher = (
            self.ensure_watcher(interval=interval, max_silence=max_silence)
            if start_watch
            else {"ok": True, "started": False}
        )
        total_workers = sum(int(target_counts[role]) for role in selected)
        return {
            "ok": all(item.get("ok") for item in records) and watcher.get("ok", False),
            "worker_roles": selected,
            "worker_instances": {
                role: int(target_counts[role])
                for role in selected
            },
            "worker_count": total_workers,
            "total_web_agents": total_workers + 1,
            "agents": records,
            "watcher": watcher,
        }

    def scale(
        self,
        requested_roles: Optional[List[str]] = None,
        requested_instances: Optional[Dict[str, int]] = None,
        worker_count: int = 0,
        max_new_roles: int = 2,
        max_instances_per_role: int = 4,
        spawn_url: str = "",
        start_watch: bool = True,
        interval: float = 15.0,
        max_silence: float = 300.0,
    ) -> Dict[str, object]:
        """Add missing-role or same-role worker instances without removing existing workers."""
        bindings = self.store.bindings()
        if "main" not in bindings:
            return {
                "ok": False,
                "error": "main swarm binding is missing; call web_agent_swarm_start first",
            }

        current_map = self._bound_worker_instances()
        current_counts = {
            role: len(current_map.get(role, []))
            for role in DEFAULT_ROLES
        }
        additions = {role: 0 for role in DEFAULT_ROLES}

        if requested_instances:
            for role, count in requested_instances.items():
                clean = str(role or "").strip().lower()
                if clean not in DEFAULT_ROLES:
                    return {"ok": False, "error": "unknown role: {0}".format(clean)}
                target = max(0, min(int(count or 0), 16))
                additions[clean] = max(0, target - int(current_counts.get(clean, 0)))
        elif requested_roles:
            for role in requested_roles:
                clean = str(role or "").strip().lower()
                if clean not in DEFAULT_ROLES:
                    return {"ok": False, "error": "unknown role: {0}".format(clean)}
                if current_counts.get(clean, 0) == 0:
                    additions[clean] = 1
        elif int(worker_count or 0):
            plan = plan_roles(self.store.tasks(), worker_count=worker_count)
            if not plan.get("ok"):
                return plan
            for role in plan["roles"]:
                if current_counts.get(role, 0) == 0:
                    additions[role] = 1
        else:
            elastic = self.elastic_plan(
                max_new_roles=max_new_roles,
                max_instances_per_role=max_instances_per_role,
            )
            if not elastic.get("ok"):
                return elastic
            for item in elastic.get("add_instances") or []:
                role = str(item.get("role") or "")
                if role in additions:
                    additions[role] += max(0, int(item.get("count") or 0))

        requested_additions = [
            {"role": role, "count": count}
            for role, count in additions.items()
            if count > 0
        ]
        if not requested_additions:
            return {
                "ok": True,
                "scaled": False,
                "added_roles": [],
                "added_workers": [],
                "added_instances": [],
                "worker_roles": self._bound_worker_roles(),
                "worker_instances": current_counts,
                "worker_count": sum(current_counts.values()),
                "total_web_agents": sum(current_counts.values()) + 1,
                "coordination": self.coordination_plan(),
            }

        tabs = self._tabs()
        target = str(spawn_url or "").strip() or self._spawn_url(tabs)
        if not target.startswith("https://chatgpt.com/"):
            return {"ok": False, "error": "spawn_url must be a chatgpt.com URL"}

        records = []
        for item in requested_additions:
            for _ in range(int(item["count"])):
                records.append(self._spawn_worker_instance(item["role"], target))

        watcher = (
            self.ensure_watcher(interval=interval, max_silence=max_silence)
            if start_watch
            else {"ok": True, "started": False}
        )
        final_map = self._bound_worker_instances()
        final_counts = {
            role: len(final_map.get(role, []))
            for role in DEFAULT_ROLES
        }
        added_workers = [
            str(item.get("name") or "")
            for item in records
            if item.get("ok")
        ]
        actual_by_role = []
        for role in DEFAULT_PRIORITY:
            count = sum(
                1
                for item in records
                if item.get("ok") and item.get("role") == role
            )
            if count:
                actual_by_role.append({"role": role, "count": count})

        return {
            "ok": all(item.get("ok") for item in records) and watcher.get("ok", False),
            "scaled": bool(added_workers),
            "added_roles": [
                item["role"]
                for item in actual_by_role
                if current_counts.get(item["role"], 0) == 0
            ],
            "added_workers": added_workers,
            "added_instances": actual_by_role,
            "previous_worker_instances": current_counts,
            "worker_roles": self._bound_worker_roles(),
            "worker_instances": final_counts,
            "worker_count": sum(final_counts.values()),
            "total_web_agents": sum(final_counts.values()) + 1,
            "agents": records,
            "watcher": watcher,
            "coordination": self.coordination_plan(),
        }

    def send(self, name: str, text: str) -> Dict[str, object]:
        tab = self._resolve(str(name))
        if not tab:
            return {"ok": False, "error": "bound conversation is not open"}
        result = self.bridge.send(int(tab["id"]), text)
        result["name"] = str(name)
        return result

    def snapshot(self, tail: int = 4) -> Dict[str, object]:
        bindings = self.store.bindings()
        agents = []
        for name in sorted(bindings):
            tab = self._resolve(name)
            item = {
                "name": name,
                "binding": bindings[name],
                "open": bool(tab),
                "tab": tab,
                "ui": None,
                "error": None,
            }
            if tab:
                inspected = self.bridge.inspect(int(tab["id"]), tail=tail)
                if inspected.get("ok"):
                    item["ui"] = inspected.get("snapshot")
                else:
                    item["error"] = inspected.get("error")
            else:
                item["error"] = "bound conversation is not currently open"
            agents.append(item)

        return {
            "ok": True,
            "bridge": self.bridge.health(),
            "agents": agents,
            "tasks": self.store.tasks(),
            "shared_status": self.store.status(),
            "supersteps": self.store.supersteps(include_pending=False),
            "coordination": self.coordination_plan(),
            "completion_truth": (
                "Browser idle means only that the page is not generating. "
                "Use durable task state and real external verification for completion."
            ),
        }
