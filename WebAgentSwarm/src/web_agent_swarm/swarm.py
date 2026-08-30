"""Browser-hosted swarm runtime."""

import copy
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from typing import Dict, List, Optional

from .bridge import BridgeClient
from .graph import compile_graph
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
        self.watcher_config_path = self.store.root / "watcher.json"
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
    def _blocker_kind(
        task: Dict[str, object],
        unresolved_dependencies: Optional[List[str]] = None,
    ) -> str:
        if unresolved_dependencies:
            return "dependency_wait"

        text = " ".join([
            str(task.get("blocker") or ""),
            str(task.get("summary") or ""),
            str(task.get("last_error") or ""),
        ]).strip().lower()
        explicit = {
            "dependency_wait",
            "external_wait",
            "human_input",
            "verification_failure",
            "recoverable_failure",
            "unknown_blocker",
        }
        for kind in explicit:
            if text.startswith(kind + ":"):
                return kind
        if any(token in text for token in (
            "human input", "user input", "approval", "confirmation",
            "人工", "用户确认", "等待用户",
        )):
            return "human_input"
        if any(token in text for token in (
            "extension", "browser", "network", "connection", "license",
            "schema unavailable", "tool unavailable", "连接", "网络", "许可证",
        )):
            return "external_wait"
        if any(token in text for token in (
            "test failed", "test failure", "verification", "regression",
            "verify", "验证失败", "回归失败",
        )):
            return "verification_failure"
        if any(token in text for token in (
            "timeout", "timed out", "crash", "exception", "stuck", "retry",
            "超时", "崩溃", "异常", "卡死", "重试",
        )):
            return "recoverable_failure"
        return "unknown_blocker"

    @classmethod
    def _task_role(
        cls,
        task: Dict[str, object],
        blocker_kind: str = "",
    ) -> str:
        status = str(task.get("status") or "").strip().lower()
        if status == "review":
            return "reviewer"
        if status == "blocked" and blocker_kind in {
            "verification_failure",
            "recoverable_failure",
            "unknown_blocker",
        }:
            return "debugger"
        owner = str(task.get("owner") or "").strip().lower()
        if owner in DEFAULT_ROLES:
            return owner
        return "implementer"

    def _bound_worker_instances(self) -> Dict[str, List[str]]:
        instances = {role: [] for role in DEFAULT_ROLES}
        for name, binding in self.store.bindings().items():
            if name == "main" or not isinstance(binding, dict) or binding.get("suspended"):
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
        max_new_roles: int = 6,
        max_instances_per_role: int = 4,
        max_total_workers: int = 6,
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
        shared_status = self.store.status()
        active_graph = self.store.graph_get()
        compiled_graph = compile_graph(
            active_graph if active_graph else None,
            task_map,
            shared_status,
        )
        if compiled_graph.get("active") and not compiled_graph.get("valid", True):
            return {
                "ok": False,
                "error": "active SwarmGraph is invalid",
                "swarm_graph": compiled_graph,
            }
        graph_gates = dict(compiled_graph.get("task_gates") or {})
        graph_task_nodes = dict(compiled_graph.get("task_node_map") or {})
        graph_nodes_by_id = {
            str(item.get("node_id") or ""): item
            for item in compiled_graph.get("nodes", [])
            if isinstance(item, dict)
        }
        nodes = []
        edges = []
        frontier = []
        barriers = []
        waits = []
        interrupts = []
        parallel_sidecars = []
        sidecar_opportunities = []
        live_sidecar_parents = set()
        pressure = {
            role: {
                "role": role,
                "responsibility": DEFAULT_ROLES[role],
                "score": 0,
                "claimable_task_ids": [],
                "leased_task_ids": [],
                "interrupt_task_ids": [],
                "sidecar_task_ids": [],
                "reasons": [],
            }
            for role in DEFAULT_ROLES
        }

        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("task_id") or "")
            status = str(task.get("status") or "").strip().lower()
            assist_parent_task_id = str(task.get("assist_parent_task_id") or "")
            assist_kind = str(task.get("assist_kind") or "")
            assist_write_policy = str(task.get("assist_write_policy") or "")
            if assist_parent_task_id and status not in {"done", "failed", "cancelled"}:
                live_sidecar_parents.add(assist_parent_task_id)
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
            blocker_kind = (
                self._blocker_kind(task, unresolved)
                if status == "blocked"
                else ""
            )
            graph_gate = graph_gates.get(task_id)
            graph_node_id = str(graph_task_nodes.get(task_id) or "")
            graph_node = graph_nodes_by_id.get(graph_node_id) or {}
            graph_role = str(graph_node.get("role") or "").strip().lower()
            role = (
                graph_role
                if graph_role in DEFAULT_ROLES
                else self._task_role(task, blocker_kind=blocker_kind)
            )
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
                blocked_record = {
                    "kind": "blocked_task",
                    "blocker_kind": blocker_kind,
                    "task_id": task_id,
                    "role": role,
                    "summary": str(task.get("summary") or ""),
                }
                if blocker_kind == "dependency_wait":
                    route = "wait_dependencies"
                    waits.append(blocked_record)
                elif blocker_kind == "external_wait":
                    route = "wait_external"
                    waits.append(blocked_record)
                elif blocker_kind == "human_input":
                    route = "interrupt_human"
                    interrupts.append(blocked_record)
                else:
                    route = "interrupt"
                    interrupts.append(blocked_record)
                    pressure[role]["score"] += 40
                    pressure[role]["interrupt_task_ids"].append(task_id)
                    pressure[role]["reasons"].append(
                        "{0} task {1}".format(blocker_kind, task_id)
                    )
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
                if isinstance(graph_gate, dict) and graph_gate.get("unselected"):
                    route = "graph_unselected"
                elif isinstance(graph_gate, dict) and not graph_gate.get("ready", False):
                    route = "wait_graph"
                    waits.append({
                        "kind": "graph_gate",
                        "task_id": task_id,
                        "role": role,
                        "graph_id": str(compiled_graph.get("graph_id") or ""),
                        "graph_node_id": graph_node_id,
                        "gate": graph_gate,
                    })
                else:
                    route = "dispatch"
                    executable = True
                    frontier.append({
                        "task_id": task_id,
                        "role": role,
                        "status": status,
                        "payload": task.get("payload"),
                        "group_id": str(task.get("group_id") or ""),
                        "recovery": bool(lease_expired),
                        "graph_id": str(compiled_graph.get("graph_id") or ""),
                        "graph_node_id": graph_node_id,
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

            if assist_parent_task_id and status not in {"done", "failed", "cancelled"}:
                pressure[role]["sidecar_task_ids"].append(task_id)
                parallel_sidecars.append({
                    "task_id": task_id,
                    "parent_task_id": assist_parent_task_id,
                    "role": role,
                    "assist_kind": assist_kind,
                    "write_policy": assist_write_policy,
                    "resource_hints": list(task.get("resource_hints") or []),
                    "canonical_root": str(task.get("canonical_root") or ""),
                    "status": status,
                    "route": route,
                    "executable": executable,
                    "claimed_by": claimed_by,
                })

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
                "blocker_kind": blocker_kind,
                "dependencies": dependencies,
                "unresolved_dependencies": unresolved,
                "claimed_by": claimed_by,
                "lease_expires_at": lease_expires,
                "attempts": int(task.get("attempts") or 0),
                "max_attempts": int(task.get("max_attempts") or 0),
                "group_id": str(task.get("group_id") or ""),
                "assist_parent_task_id": assist_parent_task_id,
                "assist_kind": assist_kind,
                "assist_write_policy": assist_write_policy,
                "resource_hints": list(task.get("resource_hints") or []),
                "canonical_root": str(task.get("canonical_root") or ""),
                "graph_node_id": graph_node_id,
                "graph_gate": copy.deepcopy(graph_gate) if isinstance(graph_gate, dict) else None,
            })

        for node in nodes:
            if str(node.get("assist_parent_task_id") or ""):
                continue
            if str(node.get("route") or "") not in {"dispatch", "leased"}:
                continue
            parent_task_id = str(node.get("task_id") or "")
            if not parent_task_id or parent_task_id in live_sidecar_parents:
                continue
            primary_role = str(node.get("role") or "")
            suggestions = []
            if primary_role == "implementer":
                suggestions = [
                    {
                        "assist_kind": "verification",
                        "role": "tester",
                        "write_policy": "experiment_only",
                        "purpose": "Run independent regression/reproduction while the primary writes code.",
                    },
                    {
                        "assist_kind": "proposal_only",
                        "role": "reviewer",
                        "write_policy": "proposal_only",
                        "purpose": "Review the same code/problem and return a patch/test proposal without competing writes.",
                    },
                ]
            elif primary_role == "debugger":
                suggestions = [
                    {
                        "assist_kind": "readonly_analysis",
                        "role": "researcher",
                        "write_policy": "readonly",
                        "purpose": "Independently mine evidence and alternative root-cause hypotheses.",
                    },
                    {
                        "assist_kind": "verification",
                        "role": "tester",
                        "write_policy": "experiment_only",
                        "purpose": "Build a minimal reproduction or verify the leading hypothesis.",
                    },
                ]
            elif primary_role == "researcher":
                suggestions = [{
                    "assist_kind": "proposal_only",
                    "role": "reviewer",
                    "write_policy": "proposal_only",
                    "purpose": "Challenge assumptions and validate evidence in parallel.",
                }]
            elif primary_role == "tester":
                suggestions = [{
                    "assist_kind": "readonly_analysis",
                    "role": "debugger",
                    "write_policy": "readonly",
                    "purpose": "Analyze failures while the tester runs the verification matrix.",
                }]
            elif primary_role == "reviewer":
                suggestions = [{
                    "assist_kind": "readonly_analysis",
                    "role": "researcher",
                    "write_policy": "readonly",
                    "purpose": "Gather missing evidence while review proceeds.",
                }]
            elif primary_role == "documenter":
                suggestions = [{
                    "assist_kind": "proposal_only",
                    "role": "reviewer",
                    "write_policy": "proposal_only",
                    "purpose": "Fact-check the draft without touching the documenter's write set.",
                }]
            if suggestions:
                task = task_map.get(parent_task_id) or {}
                sidecar_opportunities.append({
                    "parent_task_id": parent_task_id,
                    "title": str(task.get("title") or ""),
                    "primary_role": primary_role,
                    "primary_route": str(node.get("route") or ""),
                    "canonical_root": str(task.get("canonical_root") or ""),
                    "resource_hints": list(task.get("resource_hints") or []),
                    "suggestions": suggestions,
                    "policy": (
                        "Create a sidecar only when the work is genuinely orthogonal. "
                        "Do not create workers merely to fill capacity."
                    ),
                })

        priority_index = {
            role: index
            for index, role in enumerate(DEFAULT_PRIORITY)
        }
        worker_budget = max(1, min(int(max_total_workers or 1), 32))
        raw_desired = {}
        locked_minimum = {}
        for role, row in pressure.items():
            work_units = (
                len(row["claimable_task_ids"])
                + len(row["leased_task_ids"])
                + len(row["interrupt_task_ids"])
            )
            raw_desired[role] = min(per_role_limit, work_units) if work_units else 0
            locked_minimum[role] = min(per_role_limit, len(row["leased_task_ids"]))
            row["current_instances"] = int(active_instances.get(role, 0))
            row["raw_desired_instances"] = int(raw_desired[role])
            row["locked_minimum_instances"] = int(locked_minimum[role])

        effective_budget = max(worker_budget, sum(locked_minimum.values()))
        allocated = dict(locked_minimum)
        budget_order = sorted(
            pressure.values(),
            key=lambda item: (
                -int(item["score"]),
                priority_index.get(str(item["role"]), len(priority_index)),
            ),
        )
        while sum(allocated.values()) < effective_budget:
            advanced = False
            for item in budget_order:
                role = str(item["role"])
                if int(allocated.get(role, 0)) >= int(raw_desired.get(role, 0)):
                    continue
                allocated[role] = int(allocated.get(role, 0)) + 1
                advanced = True
                if sum(allocated.values()) >= effective_budget:
                    break
            if not advanced:
                break

        for role, row in pressure.items():
            row["desired_instances"] = int(allocated.get(role, 0))
            row["missing_instances"] = max(
                0,
                int(row["desired_instances"]) - int(row["current_instances"]),
            )
            row["excess_instances"] = max(
                0,
                int(row["current_instances"]) - int(row["desired_instances"]),
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
        remove_instances = [
            {
                "role": str(item["role"]),
                "count": int(item["excess_instances"]),
                "current_instances": int(item["current_instances"]),
                "desired_instances": int(item["desired_instances"]),
            }
            for item in ranked
            if int(item["excess_instances"]) > 0
        ]
        remove_roles = [
            item["role"]
            for item in remove_instances
            if int(item["desired_instances"]) == 0
        ]
        recommended_instances = dict(active_instances)
        for item in add_instances:
            recommended_instances[item["role"]] = (
                int(recommended_instances.get(item["role"], 0)) + int(item["count"])
            )
        for item in remove_instances:
            recommended_instances[item["role"]] = max(
                0,
                int(recommended_instances.get(item["role"], 0)) - int(item["count"]),
            )

        return {
            "ok": True,
            "model": (
                "durable_swarm_graph"
                if compiled_graph.get("active")
                else "durable_task_graph"
            ),
            "graph_semantics": {
                "conditional_edge": (
                    "task state plus explicit SwarmGraph gates select dispatch/wait_graph/"
                    "graph_unselected/wait_dependencies/wait_retry/leased/interrupt/terminal"
                ),
                "explicit_graph": (
                    "an activated durable SwarmGraph can bind tasks to graph nodes, override "
                    "worker roles, and gate dispatch through dependency/conditional edges"
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
                "sidecar": (
                    "JIT assist tasks run beside a current primary task; readonly/proposal/verification "
                    "sidecars do not compete for the primary write-set, while disjoint_write sidecars "
                    "must pass hierarchical MVCC claims"
                ),
            },
            "active_roles": normalized_active,
            "active_instances": active_instances,
            "task_nodes": nodes,
            "dependency_edges": edges,
            "swarm_graph": compiled_graph,
            "parallel_frontier": frontier,
            "parallel_sidecars": parallel_sidecars,
            "sidecar_opportunities": sidecar_opportunities,
            "fan_in_barriers": barriers,
            "waits": waits,
            "interrupts": interrupts,
            "role_pressure": ranked,
            "scale_recommendation": {
                "needs_scale": bool(add_instances or remove_instances),
                "needs_scale_out": bool(add_instances),
                "needs_scale_in": bool(remove_instances),
                "add_roles": add_roles,
                "add_instances": add_instances,
                "remove_roles": remove_roles,
                "remove_instances": remove_instances,
                "max_new_workers": add_limit,
                "max_instances_per_role": per_role_limit,
                "max_total_workers": worker_budget,
                "effective_worker_budget": effective_budget,
                "recommended_instances": recommended_instances,
                "basis": (
                    "desired instances only from current claimable/leased/actionable work, including "
                    "explicit JIT sidecars; dependency-wait future tasks contribute zero worker pressure. "
                    "Global budget preserves live leased capacity and excess instances may scale in safely"
                ),
            },
        }

    def elastic_plan(
        self,
        max_new_roles: int = 6,
        max_instances_per_role: int = 4,
        max_total_workers: int = 6,
    ) -> Dict[str, object]:
        """Recommend missing-role and same-role runtime instances without browser mutation."""
        coordination = self.coordination_plan(
            active_roles=None,
            max_new_roles=max_new_roles,
            max_instances_per_role=max_instances_per_role,
            max_total_workers=max_total_workers,
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
            "needs_scale_out": bool(recommendation.get("needs_scale_out")),
            "needs_scale_in": bool(recommendation.get("needs_scale_in")),
            "add_roles": list(recommendation.get("add_roles") or []),
            "add_instances": list(recommendation.get("add_instances") or []),
            "remove_roles": list(recommendation.get("remove_roles") or []),
            "remove_instances": list(recommendation.get("remove_instances") or []),
            "max_total_workers": int(recommendation.get("max_total_workers") or max_total_workers),
            "effective_worker_budget": int(
                recommendation.get("effective_worker_budget") or max_total_workers
            ),
            "coordination": coordination,
            "parallel_sidecars": list(coordination.get("parallel_sidecars") or []),
            "sidecar_opportunities": list(coordination.get("sidecar_opportunities") or []),
            "policy": (
                "Scale out from durable runnable-shard pressure and leases under the global "
                "worker budget; scale in only durably idle workers after hysteresis plus "
                "lease/superstep/UI safety checks. Browser idle alone is never a scale signal."
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
        if not isinstance(binding, dict) or binding.get("suspended"):
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
            "with another instance. If this is an assist task, obey assist_write_policy strictly: "
            "readonly/proposal_only/experiment_only sidecars must not take canonical write claims; "
            "same-file assistance returns evidence, tests, or a concrete proposal to the primary writer. "
            "Only disjoint_write sidecars may write project files, and their MVCC write claims must be "
            "non-overlapping with the primary. For any repository mutation or experiment with filesystem "
            "outputs, create an MVCC lane with swarm_lane_start after the task lease, using read/scratch "
            "claims for non-writing sidecars and write claims only when policy permits. Open the returned "
            "lane workspace in CodexPro and mutate/run commands only there; canonical mutation is guarded. "
            "Heartbeat long lanes, call swarm_lane_commit before task_release, and on conflict preserve "
            "the old lane and use swarm_lane_fork/rebase rather than overwriting. If a task carries "
            "payload/group_id, treat it as one map shard and return a compact reducer-friendly result. "
            "Be work-conserving: after releasing one task, immediately re-read the coordination plan and "
            "claim the next eligible task for this exact worker/role in the same turn when useful; stop only "
            "when no eligible work remains, a real blocker exists, or the turn budget is nearly exhausted. "
            "If CodexPro-style local development tools are connected, use them for concrete workspace "
            "work and verification. Start by reading the coordination plan and claimable task frontier."
        ).format(name, role, DEFAULT_ROLES[role])

    @staticmethod
    def _main_prompt(roles: List[str]) -> str:
        initial_roles = ", ".join(roles) if roles else "none (main-only bootstrap)"
        return (
            "You are the main Web Agent orchestrator. Initial active worker roles: {0}. "
            "Read web_agent_swarm_snapshot and web_agent_swarm_coordination_plan first. If an "
            "active SwarmGraph exists, treat it as the explicit durable execution topology: obey "
            "its wait_graph/graph_unselected gates, use swarm_graph_* tools for topology/state "
            "changes, and pass expected_revision when coordinating concurrent graph edits. If this "
            "is main-only bootstrap, use web_agent_swarm_elastic_plan and web_agent_swarm_scale "
            "to create only the worker topology justified by the durable parallel frontier. Never open "
            "workers for future dependency-wait stages. Inspect sidecar_opportunities for each current "
            "dispatch/leased primary: when there is genuinely orthogonal work, create one or two JIT helpers "
            "with swarm_task_assist before scaling. Prefer readonly_analysis, verification, or disjoint_write "
            "sidecars. If a helper needs to inspect the same files the primary is writing, make it "
            "proposal_only/readonly: one primary writer owns that write-set and the helper returns evidence, "
            "tests, or a concrete patch proposal rather than competing for the same file. Do not create "
            "sidecars merely to keep tabs busy. Treat task status plus dependency and SwarmGraph truth as graph routing: dispatch independent "
            "parallel_frontier work, respect fan_in_barriers, and treat blocked tasks as "
            "interrupts. For user input, use durable swarm_interrupt_create/resume semantics "
            "rather than chat memory. For divisible work, use swarm_task_fanout to create "
            "Send-style map shards with payloads; multiple instances of the same role may claim "
            "separate shards concurrently, and swarm_task_reduce aggregates completed shard "
            "results. For one coordinated round whose outputs must be all-or-nothing, call "
            "swarm_superstep_begin with exact worker instances, stage task_release/status_set "
            "writes, and commit only when ready_to_commit; any worker failure aborts the round. "
            "Workers must claim/lease tasks before concrete work. Repository-changing work must "
            "also use a resource-scoped MVCC lane: assign canonical_root/resources, require lane-only "
            "CodexPro mutation/experiments, and require swarm_lane_commit before task release. Resource "
            "claims are hierarchical, and commit validates base/read/write versions; conflict must fork "
            "or rebase and never overwrite canonical. Use web_agent_swarm_elastic_plan "
            "to compare durable work pressure with active instance counts, then apply "
            "web_agent_swarm_scale for recommended additions/removals under the global worker "
            "budget. Automatic scale-in uses hysteresis and must preserve bindings and refuse "
            "workers with live leases/supersteps/generation. Never scale merely because a browser "
            "tab is idle. Use swarm_checkpoint_create before risky task-graph or SwarmGraph "
            "rewrites; checkpoints include graph topology/activation and restore only when no live "
            "lease/superstep exists. Do not interrupt generating workers. "
            "Send one concise instruction per durable next action and avoid duplicate instructions. "
            "If CodexPro-style local development tools are available, use them for concrete "
            "repository work and verification rather than guessing."
        ).format(initial_roles)

    def _send_when_ready(
        self,
        tab_id: int,
        text: str,
        timeout: float = 20.0,
        command_id: str = "",
    ) -> Dict[str, object]:
        deadline = time.time() + max(1.0, float(timeout))
        active_command_id = str(command_id or "").strip() or uuid.uuid4().hex
        last = {
            "ok": False,
            "error": "send timed out",
            "command_id": active_command_id,
        }
        delay = 0.25
        attempts = 0
        while time.time() < deadline:
            inspected = self.bridge.inspect(tab_id, tail=1)
            if inspected.get("ok"):
                snapshot = inspected.get("snapshot") or {}
                if snapshot.get("generating"):
                    return {
                        "ok": False,
                        "busy": True,
                        "stuck_generating": bool(snapshot.get("stuckGenerating")),
                        "command_id": active_command_id,
                        "error": "tab is generating",
                    }
            attempts += 1
            last = self.bridge.send(
                tab_id,
                text,
                command_id=active_command_id,
            )
            last["command_id"] = active_command_id
            last["attempts"] = attempts
            if last.get("ok"):
                return last
            time.sleep(delay)
            delay = min(delay * 2.0, 2.0)
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
        interval: float = 300.0,
        max_silence: float = 300.0,
    ) -> Dict[str, object]:
        desired = {
            "interval": max(2.0, float(interval)),
            "max_silence": max(0.0, float(max_silence)),
        }
        pid = 0
        if self.watcher_pid_path.is_file():
            try:
                pid = int(self.watcher_pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = 0

        restarted = False
        if pid and self._pid_alive(pid):
            current = {}
            if self.watcher_config_path.is_file():
                try:
                    loaded = json.loads(self.watcher_config_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        current = loaded
                except (OSError, ValueError):
                    current = {}
            if current == desired:
                return {
                    "ok": True,
                    "already_running": True,
                    "pid": pid,
                    "config": desired,
                    "log": str(self.watcher_log_path),
                }

            restarted = True
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            deadline = time.time() + 2.0
            while time.time() < deadline and self._pid_alive(pid):
                time.sleep(0.05)
            if self._pid_alive(pid):
                return {
                    "ok": False,
                    "error": "watcher config changed but old watcher could not be stopped",
                    "pid": pid,
                    "config": current,
                }

        self.store.root.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "web_agent_swarm.watcher",
            "--interval",
            str(desired["interval"]),
            "--max-silence",
            str(desired["max_silence"]),
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
        self.watcher_config_path.write_text(
            json.dumps(desired, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {
            "ok": True,
            "already_running": False,
            "restarted": restarted,
            "pid": process.pid,
            "config": desired,
            "log": str(self.watcher_log_path),
        }

    def _worker_suspend_guard(self, name: str) -> Dict[str, object]:
        clean_name = str(name or "").strip()
        if not clean_name or clean_name == "main":
            return {
                "ok": False,
                "name": clean_name,
                "reasons": ["main_or_invalid_worker"],
            }

        now = time.time()
        tasks = self.store.tasks()
        leased_tasks = [
            str(task.get("task_id") or "")
            for task in tasks
            if isinstance(task, dict)
            and str(task.get("status") or "").lower() == "active"
            and str(task.get("claimed_by") or "") == clean_name
            and float(task.get("lease_expires_at") or 0.0) > now
        ]
        active_steps = [
            str(step.get("step_id") or "")
            for step in self.store.supersteps(include_pending=False)
            if isinstance(step, dict)
            and step.get("status") in {"open", "ready"}
            and clean_name in list(step.get("participants") or [])
        ]
        reasons = []
        if leased_tasks:
            reasons.append("active_task_lease")
        if active_steps:
            reasons.append("active_superstep")
        return {
            "ok": not reasons,
            "name": clean_name,
            "reasons": reasons,
            "leased_task_ids": leased_tasks,
            "active_supersteps": active_steps,
        }

    def suspend(self, name: str, close_tab: bool = True) -> Dict[str, object]:
        clean_name = str(name or "").strip()
        binding = self.store.bindings().get(clean_name)
        if clean_name == "main" or not isinstance(binding, dict):
            return {
                "ok": False,
                "name": clean_name,
                "error": "worker binding not found or main cannot be suspended",
            }
        if binding.get("suspended"):
            return {
                "ok": True,
                "name": clean_name,
                "suspended": True,
                "already_suspended": True,
                "tab_closed": False,
            }

        guard = self._worker_suspend_guard(clean_name)
        if not guard.get("ok"):
            return {
                "ok": False,
                "name": clean_name,
                "safe_to_suspend": False,
                "guard": guard,
                "error": "worker owns an active durable lease/superstep",
            }

        tab = self._resolve(clean_name)
        if tab:
            inspected = self.bridge.inspect(int(tab["id"]), tail=1)
            if not inspected.get("ok"):
                return {
                    "ok": False,
                    "name": clean_name,
                    "safe_to_suspend": False,
                    "error": inspected.get("error") or "worker tab state could not be inspected",
                }
            snapshot = inspected.get("snapshot") or {}
            if snapshot.get("generating"):
                return {
                    "ok": False,
                    "name": clean_name,
                    "safe_to_suspend": False,
                    "stuck_generating": bool(snapshot.get("stuckGenerating")),
                    "error": "worker tab is generating",
                }
            if close_tab:
                closed = self.bridge.close_tab(int(tab["id"]))
                if not closed.get("ok"):
                    return {
                        "ok": False,
                        "name": clean_name,
                        "safe_to_suspend": False,
                        "error": closed.get("error") or "failed to close worker tab",
                    }

        updated = dict(binding)
        updated["suspended"] = True
        updated["suspended_at"] = time.time()
        if tab and str(tab.get("url") or ""):
            updated["url_at_bind"] = str(tab.get("url") or "")
        self.store.bind(clean_name, updated)
        return {
            "ok": True,
            "name": clean_name,
            "role": str(updated.get("role") or ""),
            "suspended": True,
            "tab_closed": bool(tab and close_tab),
            "guard": guard,
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
        suspended_names = sorted(
            str(name)
            for name, binding in bindings.items()
            if isinstance(binding, dict)
            and binding.get("suspended")
            and str(binding.get("role") or "").strip().lower() == clean_role
        )
        name = (
            suspended_names[0]
            if suspended_names
            else self._next_worker_name(clean_role, bindings)
        )
        existing = bindings.get(name)
        reopen_url = (
            str(existing.get("url_at_bind") or "")
            if isinstance(existing, dict)
            else ""
        )
        opened = self.bridge.open_tab(reopen_url or target_url, active=False)
        if not opened.get("ok") or not opened.get("tab"):
            return {
                "ok": False,
                "name": name,
                "role": clean_role,
                "action": "resume_failed" if reopen_url else "create_failed",
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
                "action": "resumed" if reopen_url else "created",
                "error": sent.get("error"),
            }
        stable = self._wait_conversation(int(tab["id"]))
        if not stable:
            return {
                "ok": False,
                "name": name,
                "role": clean_role,
                "action": "resumed" if reopen_url else "created",
                "error": "conversation URL did not stabilize",
            }
        self._bind(name, stable, "worker", clean_role)
        return {
            "ok": True,
            "name": name,
            "role": clean_role,
            "kind": "worker",
            "action": "resumed" if reopen_url else "created",
            "tab": stable,
        }

    def start(
        self,
        roles: Optional[List[str]] = None,
        instances: Optional[Dict[str, int]] = None,
        spawn_url: str = "",
        start_watch: bool = True,
        interval: float = 300.0,
        max_silence: float = 300.0,
    ) -> Dict[str, object]:
        selected = []
        target_counts = {}
        for role in roles or []:
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

        desired_worker_names = set()
        for role in selected:
            count = max(1, int(target_counts.get(role, 1)))
            target_counts[role] = count
            for index in range(1, count + 1):
                desired_worker_names.add(self._worker_instance_name(role, index))

        current = self.store.bindings()
        deactivated = []
        deactivation_blocked = []
        for stale, binding in current.items():
            if stale == "main" or stale in desired_worker_names:
                continue
            if isinstance(binding, dict) and binding.get("suspended"):
                continue
            suspended = self.suspend(stale, close_tab=True)
            if suspended.get("ok"):
                deactivated.append(stale)
            else:
                deactivation_blocked.append({
                    "name": stale,
                    "error": suspended.get("error"),
                    "guard": suspended.get("guard"),
                    "stuck_generating": suspended.get("stuck_generating", False),
                })

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
            "ok": (
                all(item.get("ok") for item in records)
                and watcher.get("ok", False)
                and not deactivation_blocked
            ),
            "bootstrap_mode": "main_only" if not selected else "explicit_workers",
            "worker_roles": selected,
            "worker_instances": {
                role: int(target_counts[role])
                for role in selected
            },
            "worker_count": total_workers,
            "total_web_agents": total_workers + 1,
            "deactivated_workers": deactivated,
            "scale_in_blocked": deactivation_blocked,
            "agents": records,
            "watcher": watcher,
        }

    def scale(
        self,
        requested_roles: Optional[List[str]] = None,
        requested_instances: Optional[Dict[str, int]] = None,
        worker_count: int = 0,
        max_new_roles: int = 6,
        max_instances_per_role: int = 4,
        max_total_workers: int = 6,
        scale_in_grace_seconds: float = 300.0,
        spawn_url: str = "",
        start_watch: bool = True,
        interval: float = 300.0,
        max_silence: float = 300.0,
    ) -> Dict[str, object]:
        """Scale worker instances in both directions using durable-pressure targets."""
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
        target_counts = dict(current_counts)
        elastic = None
        automatic_scaling = (
            requested_instances is None
            and not requested_roles
            and not int(worker_count or 0)
        )
        grace_seconds = max(0.0, float(scale_in_grace_seconds or 0.0))

        if requested_instances is not None:
            for role, count in requested_instances.items():
                clean = str(role or "").strip().lower()
                if clean not in DEFAULT_ROLES:
                    return {"ok": False, "error": "unknown role: {0}".format(clean)}
                target_counts[clean] = max(0, min(int(count or 0), 16))
        elif requested_roles:
            for role in requested_roles:
                clean = str(role or "").strip().lower()
                if clean not in DEFAULT_ROLES:
                    return {"ok": False, "error": "unknown role: {0}".format(clean)}
                target_counts[clean] = max(1, int(target_counts.get(clean, 0)))
        elif int(worker_count or 0):
            plan = plan_roles(self.store.tasks(), worker_count=worker_count)
            if not plan.get("ok"):
                return plan
            for role in plan["roles"]:
                target_counts[role] = max(1, int(target_counts.get(role, 0)))
        else:
            elastic = self.elastic_plan(
                max_new_roles=max_new_roles,
                max_instances_per_role=max_instances_per_role,
                max_total_workers=max_total_workers,
            )
            if not elastic.get("ok"):
                return elastic
            recommended = (
                (elastic.get("coordination") or {})
                .get("scale_recommendation", {})
                .get("recommended_instances", {})
            )
            for role in DEFAULT_ROLES:
                target_counts[role] = max(
                    0,
                    min(int(recommended.get(role, current_counts.get(role, 0)) or 0), 16),
                )

        additions = {
            role: max(0, int(target_counts[role]) - int(current_counts[role]))
            for role in DEFAULT_ROLES
        }
        removals = {
            role: max(0, int(current_counts[role]) - int(target_counts[role]))
            for role in DEFAULT_ROLES
        }

        removed_workers = []
        scale_in_blocked = []
        scale_in_deferred = []
        scale_in_skipped = []
        now = time.time()

        for role in DEFAULT_PRIORITY:
            if int(removals.get(role, 0)) > 0:
                continue
            for name in current_map.get(role, []):
                binding = self.store.bindings().get(name)
                if isinstance(binding, dict) and binding.get("excess_since"):
                    updated = dict(binding)
                    updated.pop("excess_since", None)
                    self.store.bind(name, updated)

        for role in DEFAULT_PRIORITY:
            remaining = int(removals.get(role, 0))
            if remaining <= 0:
                continue
            candidates = list(reversed(current_map.get(role, [])))
            for name in candidates:
                if remaining <= 0:
                    break

                guard = self._worker_suspend_guard(name)
                if not guard.get("ok"):
                    scale_in_skipped.append({
                        "name": name,
                        "role": role,
                        "error": "worker has a live durable lease/superstep",
                        "guard": guard,
                    })
                    continue

                if automatic_scaling and grace_seconds > 0:
                    binding = self.store.bindings().get(name)
                    since = (
                        float(binding.get("excess_since") or 0.0)
                        if isinstance(binding, dict)
                        else 0.0
                    )
                    if since <= 0.0:
                        updated = dict(binding or {})
                        updated["excess_since"] = now
                        self.store.bind(name, updated)
                        since = now
                    elapsed = max(0.0, now - since)
                    if elapsed < grace_seconds:
                        scale_in_deferred.append({
                            "name": name,
                            "role": role,
                            "excess_since": since,
                            "elapsed_seconds": elapsed,
                            "grace_seconds": grace_seconds,
                            "retry_after": since + grace_seconds,
                        })
                        remaining -= 1
                        continue

                result = self.suspend(name, close_tab=True)
                if result.get("ok"):
                    removed_workers.append(name)
                    remaining -= 1
                else:
                    scale_in_skipped.append({
                        "name": name,
                        "role": role,
                        "error": result.get("error"),
                        "guard": result.get("guard"),
                        "stuck_generating": result.get("stuck_generating", False),
                    })
            if remaining > 0:
                scale_in_blocked.append({
                    "role": role,
                    "count_unmet": remaining,
                    "error": "not enough safe worker instances were available for scale-in",
                })

        requested_additions = [
            {"role": role, "count": int(count)}
            for role, count in additions.items()
            if int(count) > 0
        ]
        records = []
        if requested_additions:
            tabs = self._tabs()
            target = str(spawn_url or "").strip() or self._spawn_url(tabs)
            if not target.startswith("https://chatgpt.com/"):
                return {"ok": False, "error": "spawn_url must be a chatgpt.com URL"}
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
            "ok": (
                all(item.get("ok") for item in records)
                and watcher.get("ok", False)
                and not scale_in_blocked
            ),
            "scaled": bool(added_workers or removed_workers),
            "added_roles": [
                item["role"]
                for item in actual_by_role
                if current_counts.get(item["role"], 0) == 0
            ],
            "added_workers": added_workers,
            "added_instances": actual_by_role,
            "removed_workers": removed_workers,
            "removed_instances": [
                {
                    "role": role,
                    "count": max(0, current_counts[role] - final_counts[role]),
                }
                for role in DEFAULT_PRIORITY
                if current_counts[role] > final_counts[role]
            ],
            "scale_in_blocked": scale_in_blocked,
            "scale_in_deferred": scale_in_deferred,
            "scale_in_skipped": scale_in_skipped,
            "scale_in_grace_seconds": grace_seconds,
            "max_total_workers": max(1, min(int(max_total_workers or 1), 32)),
            "requested_target_instances": target_counts,
            "previous_worker_instances": current_counts,
            "worker_roles": self._bound_worker_roles(),
            "worker_instances": final_counts,
            "worker_count": sum(final_counts.values()),
            "total_web_agents": sum(final_counts.values()) + 1,
            "agents": records,
            "watcher": watcher,
            "coordination": self.coordination_plan(max_total_workers=max_total_workers),
        }

    def send(self, name: str, text: str) -> Dict[str, object]:
        tab = self._resolve(str(name))
        if not tab:
            return {"ok": False, "error": "bound conversation is not open"}
        result = self._send_when_ready(
            int(tab["id"]),
            text,
            timeout=10.0,
        )
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

        coordination = self.coordination_plan()
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
        for task in self.store.tasks():
            item = dict(task)
            task_id = str(item.get("task_id") or "")
            if str(item.get("status") or "").lower() == "blocked":
                item["blocker_kind"] = kind_by_task.get(task_id) or self._blocker_kind(item)
            item["route"] = route_by_task.get(task_id) or "terminal"
            tasks.append(item)

        durable = self.store.load()
        return {
            "ok": True,
            "bridge": self.bridge.health(),
            "agents": agents,
            "tasks": tasks,
            "shared_status": self.store.status(),
            "supersteps": self.store.supersteps(include_pending=False),
            "interrupts": self.store.interrupts(),
            "checkpoints": self.store.checkpoints(),
            "graphs": self.store.graphs(),
            "active_graph": self.store.graph_get(),
            "runs": list(durable.get("runs", {}).values()),
            "lanes": list(durable.get("lanes", {}).values()),
            "resource_claims": list(durable.get("resource_claims", {}).values()),
            "commits": list(durable.get("commits", {}).values()),
            "resource_versions": durable.get("resource_versions", {}),
            "canonical_epoch": int(durable.get("canonical_epoch") or 0),
            "canonical_epochs": durable.get("canonical_epochs", {}),
            "coordination": coordination,
            "completion_truth": (
                "Browser idle means only that the page is not generating. "
                "Use durable task state and real external verification for completion."
            ),
        }
