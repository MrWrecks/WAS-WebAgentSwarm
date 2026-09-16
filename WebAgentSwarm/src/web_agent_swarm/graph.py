"""Pure helpers for explicit durable SwarmGraph topology and routing."""

import copy
import json
from typing import Dict, List, Optional, Tuple


NODE_KINDS = {"task", "router", "join", "agent", "tool", "control"}
NODE_STATUSES = {"ready", "running", "blocked", "done", "failed", "skipped"}
TERMINAL_NODE_STATUSES = {"done", "failed", "skipped"}
EDGE_KINDS = {"dependency", "conditional"}
CONDITION_SOURCES = {"source_status", "source_state", "task_result", "shared_status"}
CONDITION_OPS = {
    "eq",
    "ne",
    "in",
    "not_in",
    "exists",
    "truthy",
    "falsy",
    "gt",
    "gte",
    "lt",
    "lte",
}


def _clean_id(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("{0} is required".format(field))
    if len(text) > 160:
        raise ValueError("{0} is too long".format(field))
    if any(ch.isspace() for ch in text):
        raise ValueError("{0} cannot contain whitespace".format(field))
    return text


def _json_value(value: object, field: str) -> object:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("{0} must be JSON serializable".format(field)) from exc
    return copy.deepcopy(value)


def normalize_node(
    node_id: str,
    kind: str = "task",
    task_id: str = "",
    role: str = "",
    label: str = "",
    status: str = "ready",
    state: object = None,
    metadata: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    clean_id = _clean_id(node_id, "node_id")
    clean_kind = str(kind or "task").strip().lower()
    if clean_kind not in NODE_KINDS:
        raise ValueError("invalid graph node kind")
    clean_status = str(status or "ready").strip().lower()
    if clean_status not in NODE_STATUSES:
        raise ValueError("invalid graph node status")
    clean_task = str(task_id or "").strip()
    clean_role = str(role or "").strip().lower()
    if clean_kind == "task" and not clean_task:
        raise ValueError("task graph node requires task_id")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("node metadata must be an object")
    return {
        "node_id": clean_id,
        "kind": clean_kind,
        "task_id": clean_task,
        "role": clean_role,
        "label": str(label or "").strip(),
        "status": clean_status,
        "state": _json_value(state, "node state"),
        "metadata": _json_value(metadata or {}, "node metadata"),
    }


def normalize_condition(condition: object) -> Dict[str, object]:
    if not isinstance(condition, dict):
        raise ValueError("conditional edge requires a condition object")
    source = str(condition.get("source") or "").strip().lower()
    if source not in CONDITION_SOURCES:
        raise ValueError("invalid condition source")
    op = str(condition.get("op") or "eq").strip().lower()
    if op not in CONDITION_OPS:
        raise ValueError("invalid condition operator")
    normalized = {
        "source": source,
        "op": op,
    }
    if source == "shared_status":
        key = str(condition.get("key") or "").strip()
        if not key:
            raise ValueError("shared_status condition requires key")
        normalized["key"] = key
    path = condition.get("path")
    if path not in {None, ""}:
        normalized["path"] = str(path)
    if op not in {"exists", "truthy", "falsy"}:
        normalized["value"] = _json_value(condition.get("value"), "condition value")
    return normalized


def normalize_edge(
    edge_id: str,
    source: str,
    target: str,
    kind: str = "dependency",
    condition: object = None,
    label: str = "",
    metadata: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    clean_id = _clean_id(edge_id, "edge_id")
    clean_source = _clean_id(source, "source")
    clean_target = _clean_id(target, "target")
    clean_kind = str(kind or "dependency").strip().lower()
    if clean_kind not in EDGE_KINDS:
        raise ValueError("invalid graph edge kind")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("edge metadata must be an object")
    normalized_condition = None
    if clean_kind == "conditional":
        normalized_condition = normalize_condition(condition)
    elif condition is not None and condition != {}:
        raise ValueError("dependency edge cannot carry a condition")
    return {
        "edge_id": clean_id,
        "source": clean_source,
        "target": clean_target,
        "kind": clean_kind,
        "condition": normalized_condition,
        "label": str(label or "").strip(),
        "metadata": _json_value(metadata or {}, "edge metadata"),
    }


def _path_get(value: object, path: str) -> Tuple[bool, object]:
    if not path:
        return True, value
    current = value
    for token in str(path).split("."):
        if isinstance(current, dict) and token in current:
            current = current[token]
            continue
        if isinstance(current, list):
            try:
                current = current[int(token)]
                continue
            except (ValueError, IndexError):
                return False, None
        return False, None
    return True, current


def _compare(actual: object, exists: bool, op: str, expected: object = None) -> bool:
    if op == "exists":
        return exists
    if op == "truthy":
        return exists and bool(actual)
    if op == "falsy":
        return (not exists) or not bool(actual)
    if not exists:
        return False
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        try:
            return actual in expected
        except TypeError:
            return False
    if op == "not_in":
        try:
            return actual not in expected
        except TypeError:
            return False
    try:
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
    except TypeError:
        return False
    return False


def runtime_node(
    node: Dict[str, object],
    task_map: Dict[str, Dict[str, object]],
) -> Dict[str, object]:
    result = copy.deepcopy(node)
    task_id = str(node.get("task_id") or "")
    task = task_map.get(task_id) if task_id else None
    if isinstance(task, dict):
        result["status"] = str(task.get("status") or "ready").lower()
        result["task_result"] = copy.deepcopy(task.get("result"))
        result["task_summary"] = str(task.get("summary") or "")
    else:
        result["task_result"] = None
        result["task_summary"] = ""
    return result


def evaluate_condition(
    condition: Dict[str, object],
    source_node: Dict[str, object],
    shared_status: Dict[str, object],
) -> bool:
    source = str(condition.get("source") or "")
    op = str(condition.get("op") or "eq")
    exists = True
    actual = None

    if source == "source_status":
        actual = source_node.get("status")
    elif source == "source_state":
        exists, actual = _path_get(source_node.get("state"), str(condition.get("path") or ""))
    elif source == "task_result":
        exists, actual = _path_get(
            source_node.get("task_result"),
            str(condition.get("path") or ""),
        )
    elif source == "shared_status":
        key = str(condition.get("key") or "")
        record = shared_status.get(key)
        if not isinstance(record, dict) or "value" not in record:
            exists = False
            actual = None
        else:
            exists, actual = _path_get(
                record.get("value"),
                str(condition.get("path") or ""),
            )
    else:
        return False

    return _compare(actual, exists, op, condition.get("value"))


def graph_validate(
    graph: Dict[str, object],
    tasks: Optional[Dict[str, Dict[str, object]]] = None,
) -> Dict[str, object]:
    nodes = graph.get("nodes") or {}
    edges = graph.get("edges") or {}
    errors: List[str] = []
    warnings: List[str] = []
    task_bindings: Dict[str, str] = {}

    if not isinstance(nodes, dict):
        errors.append("nodes must be an object")
        nodes = {}
    if not isinstance(edges, dict):
        errors.append("edges must be an object")
        edges = {}
    if not nodes:
        warnings.append("graph has no nodes")

    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            errors.append("node {0} is invalid".format(node_id))
            continue
        if str(node.get("node_id") or "") != str(node_id):
            errors.append("node key/id mismatch: {0}".format(node_id))
        try:
            normalized = normalize_node(
                node_id=str(node_id),
                kind=str(node.get("kind") or "task"),
                task_id=str(node.get("task_id") or ""),
                role=str(node.get("role") or ""),
                label=str(node.get("label") or ""),
                status=str(node.get("status") or "ready"),
                state=node.get("state"),
                metadata=node.get("metadata") or {},
            )
        except ValueError as exc:
            errors.append("node {0}: {1}".format(node_id, exc))
            continue
        task_id = str(normalized.get("task_id") or "")
        if task_id:
            previous = task_bindings.get(task_id)
            if previous and previous != str(node_id):
                errors.append(
                    "task {0} is bound to multiple graph nodes: {1}, {2}".format(
                        task_id, previous, node_id
                    )
                )
            task_bindings[task_id] = str(node_id)
            if tasks is not None and task_id not in tasks:
                errors.append("node {0} references unknown task {1}".format(node_id, task_id))

    unconditional_adj: Dict[str, List[str]] = {str(node_id): [] for node_id in nodes}
    incoming: Dict[str, int] = {str(node_id): 0 for node_id in nodes}
    outgoing: Dict[str, int] = {str(node_id): 0 for node_id in nodes}

    for edge_id, edge in edges.items():
        if not isinstance(edge, dict):
            errors.append("edge {0} is invalid".format(edge_id))
            continue
        if str(edge.get("edge_id") or "") != str(edge_id):
            errors.append("edge key/id mismatch: {0}".format(edge_id))
        try:
            normalized = normalize_edge(
                edge_id=str(edge_id),
                source=str(edge.get("source") or ""),
                target=str(edge.get("target") or ""),
                kind=str(edge.get("kind") or "dependency"),
                condition=edge.get("condition"),
                label=str(edge.get("label") or ""),
                metadata=edge.get("metadata") or {},
            )
        except ValueError as exc:
            errors.append("edge {0}: {1}".format(edge_id, exc))
            continue
        source = str(normalized["source"])
        target = str(normalized["target"])
        if source not in nodes:
            errors.append("edge {0} references unknown source {1}".format(edge_id, source))
        if target not in nodes:
            errors.append("edge {0} references unknown target {1}".format(edge_id, target))
        if source == target:
            warnings.append("edge {0} is a self-loop".format(edge_id))
        if source in nodes and target in nodes:
            incoming[target] += 1
            outgoing[source] += 1
            if normalized["kind"] == "dependency":
                unconditional_adj[source].append(target)

    visiting = set()
    visited = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for nxt in unconditional_adj.get(node_id, []):
            if visit(nxt):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    if any(visit(node_id) for node_id in unconditional_adj):
        errors.append("unconditional dependency edges contain a cycle")

    entry_nodes = sorted([node_id for node_id, count in incoming.items() if count == 0])
    terminal_nodes = sorted([node_id for node_id, count in outgoing.items() if count == 0])
    conditional_cycle_possible = any(
        isinstance(edge, dict)
        and edge.get("kind") == "conditional"
        and edge.get("source") == edge.get("target")
        for edge in edges.values()
    )
    if conditional_cycle_possible:
        warnings.append("conditional self-loop requires manual task/node reset for another iteration")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "task_bindings": task_bindings,
        "entry_nodes": entry_nodes,
        "terminal_nodes": terminal_nodes,
    }


def compile_graph(
    graph: Optional[Dict[str, object]],
    task_map: Dict[str, Dict[str, object]],
    shared_status: Dict[str, object],
) -> Dict[str, object]:
    if not isinstance(graph, dict):
        return {
            "active": False,
            "graph_id": "",
            "revision": 0,
            "nodes": [],
            "edges": [],
            "task_gates": {},
            "task_node_map": {},
        }

    raw_nodes = graph.get("nodes") or {}
    raw_edges = graph.get("edges") or {}
    runtime_nodes = {
        str(node_id): runtime_node(node, task_map)
        for node_id, node in raw_nodes.items()
        if isinstance(node, dict)
    }
    incoming: Dict[str, List[Dict[str, object]]] = {
        node_id: [] for node_id in runtime_nodes
    }
    edge_states: List[Dict[str, object]] = []

    for edge_id, edge in raw_edges.items():
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        source = runtime_nodes.get(source_id)
        target = runtime_nodes.get(target_id)
        record = copy.deepcopy(edge)
        record["edge_id"] = str(edge_id)
        record["status"] = "invalid"
        if not isinstance(source, dict) or not isinstance(target, dict):
            edge_states.append(record)
            continue

        kind = str(edge.get("kind") or "dependency")
        source_status = str(source.get("status") or "ready")
        if kind == "dependency":
            if source_status in {"done", "skipped"}:
                record["status"] = "satisfied"
            elif source_status == "failed":
                record["status"] = "failed_source"
            else:
                record["status"] = "pending"
        elif kind == "conditional":
            if source_status not in TERMINAL_NODE_STATUSES:
                record["status"] = "pending"
            else:
                selected = evaluate_condition(
                    edge.get("condition") or {},
                    source,
                    shared_status,
                )
                record["status"] = "selected" if selected else "rejected"
        incoming[target_id].append(record)
        edge_states.append(record)

    task_gates: Dict[str, Dict[str, object]] = {}
    task_node_map: Dict[str, str] = {}
    node_records: List[Dict[str, object]] = []

    for node_id, node in runtime_nodes.items():
        item = copy.deepcopy(node)
        item["node_id"] = node_id
        incoming_edges = incoming.get(node_id, [])
        dependencies = [
            edge for edge in incoming_edges if edge.get("kind") == "dependency"
        ]
        conditionals = [
            edge for edge in incoming_edges if edge.get("kind") == "conditional"
        ]
        unsatisfied = [
            edge for edge in dependencies
            if edge.get("status") != "satisfied"
        ]
        selected = [
            edge for edge in conditionals
            if edge.get("status") == "selected"
        ]
        pending_conditional = [
            edge for edge in conditionals
            if edge.get("status") == "pending"
        ]
        rejected = [
            edge for edge in conditionals
            if edge.get("status") == "rejected"
        ]
        conditional_ok = (not conditionals) or bool(selected)
        unselected = bool(conditionals) and not selected and not pending_conditional
        ready = not unsatisfied and conditional_ok
        gate = {
            "node_id": node_id,
            "ready": ready,
            "unselected": unselected,
            "dependency_edge_ids": [str(edge.get("edge_id") or "") for edge in dependencies],
            "unsatisfied_edge_ids": [str(edge.get("edge_id") or "") for edge in unsatisfied],
            "conditional_edge_ids": [str(edge.get("edge_id") or "") for edge in conditionals],
            "selected_edge_ids": [str(edge.get("edge_id") or "") for edge in selected],
            "pending_conditional_edge_ids": [
                str(edge.get("edge_id") or "") for edge in pending_conditional
            ],
            "rejected_edge_ids": [str(edge.get("edge_id") or "") for edge in rejected],
        }
        item["gate"] = gate
        node_records.append(item)

        task_id = str(node.get("task_id") or "")
        if task_id:
            task_gates[task_id] = copy.deepcopy(gate)
            task_node_map[task_id] = node_id

    validation = graph_validate(graph, tasks=task_map)
    return {
        "active": True,
        "graph_id": str(graph.get("graph_id") or ""),
        "name": str(graph.get("name") or ""),
        "revision": int(graph.get("revision") or 0),
        "valid": bool(validation.get("ok")),
        "validation": validation,
        "nodes": sorted(node_records, key=lambda item: str(item.get("node_id") or "")),
        "edges": sorted(edge_states, key=lambda item: str(item.get("edge_id") or "")),
        "task_gates": task_gates,
        "task_node_map": task_node_map,
    }
