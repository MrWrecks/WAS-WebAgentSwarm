"""Optional FastMCP server for Web Agent Swarm."""

try:
    from fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit(
        'FastMCP is not installed. Run: pip install -e ".[mcp]"'
    ) from exc

from .benchmark import benchmark_history, run_scheduler_benchmark
from .bridge import BridgeClient
from .mvcc import LaneManager
from .state import DurableStore
from .swarm import SwarmRuntime


mcp = FastMCP("Web Agent Swarm")
bridge = BridgeClient()
store = DurableStore()
lanes = LaneManager(store)
swarm = SwarmRuntime(bridge=bridge, store=store)


@mcp.tool
def web_agent_swarm_benchmark(
    record: bool = False,
    journal_path: str = "",
):
    """Run deterministic scheduler episodes and optionally record a reproducibility study."""
    return run_scheduler_benchmark(record=record, journal_path=journal_path)


@mcp.tool
def web_agent_swarm_benchmark_history(
    limit: int = 20,
    journal_path: str = "",
):
    """List recent local scheduler benchmark studies."""
    return benchmark_history(limit=limit, journal_path=journal_path)


@mcp.tool
def web_agent_bridge_health():
    """Check localhost bridge and browser-extension connectivity."""
    return bridge.health()


@mcp.tool
def web_agent_tabs():
    """List browser tabs visible to the extension."""
    return bridge.tabs()


@mcp.tool
def web_agent_inspect(tab_id: int, tail: int = 6):
    """Inspect bounded visible state for one exact ChatGPT tab."""
    return bridge.inspect(tab_id, tail)


@mcp.tool
def web_agent_send(tab_id: int, text: str):
    """Send one exact message to one exact ChatGPT tab."""
    return bridge.send(tab_id, text)


@mcp.tool
def web_agent_swarm_plan(worker_count: int = 0, requested_roles: list = None):
    """Read-only worker-count and role proposal. Does not open tabs or send messages."""
    return swarm.plan(worker_count, requested_roles)


@mcp.tool
def web_agent_swarm_start(
    roles: list = None,
    instances: dict = None,
    spawn_url: str = "",
    start_watch: bool = True,
    interval: float = 300.0,
    max_silence: float = 300.0,
):
    """Start the confirmed swarm with optional per-role instance counts."""
    return swarm.start(
        roles,
        instances=instances,
        spawn_url=spawn_url,
        start_watch=start_watch,
        interval=interval,
        max_silence=max_silence,
    )


@mcp.tool
def web_agent_swarm_snapshot(tail: int = 4):
    """Return browser worker state plus durable shared tasks/status."""
    return swarm.snapshot(tail)


@mcp.tool
def web_agent_swarm_coordination_plan(
    active_roles: list = None,
    max_new_roles: int = 6,
    max_instances_per_role: int = 4,
    max_total_workers: int = 6,
):
    """Return task routing, shard frontier, leases, reducers and budgeted instance pressure."""
    return swarm.coordination_plan(
        active_roles=active_roles,
        max_new_roles=max_new_roles,
        max_instances_per_role=max_instances_per_role,
        max_total_workers=max_total_workers,
    )


@mcp.tool
def web_agent_swarm_ensure_parallelism(
    max_sidecars_per_primary: int = 2,
    max_new_sidecars: int = 4,
):
    """Materialize bounded safe JIT sidecars for runnable primary tasks."""
    return swarm.ensure_parallelism(
        max_sidecars_per_primary=max_sidecars_per_primary,
        max_new_sidecars=max_new_sidecars,
    )


@mcp.tool
def web_agent_swarm_elastic_plan(
    max_new_roles: int = 6,
    max_instances_per_role: int = 4,
    max_total_workers: int = 6,
):
    """Recommend budgeted elastic worker topology from durable shard pressure."""
    return swarm.elastic_plan(
        max_new_roles=max_new_roles,
        max_instances_per_role=max_instances_per_role,
        max_total_workers=max_total_workers,
    )


@mcp.tool
def web_agent_swarm_scale(
    requested_roles: list = None,
    requested_instances: dict = None,
    worker_count: int = 0,
    max_new_roles: int = 6,
    max_instances_per_role: int = 4,
    max_total_workers: int = 6,
    scale_in_grace_seconds: float = 300.0,
    spawn_url: str = "",
    start_watch: bool = True,
    interval: float = 300.0,
    max_silence: float = 300.0,
):
    """Elastically add or safely suspend worker instances toward requested/durable targets."""
    return swarm.scale(
        requested_roles=requested_roles,
        requested_instances=requested_instances,
        worker_count=worker_count,
        max_new_roles=max_new_roles,
        max_instances_per_role=max_instances_per_role,
        max_total_workers=max_total_workers,
        scale_in_grace_seconds=scale_in_grace_seconds,
        spawn_url=spawn_url,
        start_watch=start_watch,
        interval=interval,
        max_silence=max_silence,
    )


@mcp.tool
def web_agent_swarm_send(name: str, text: str):
    """Send to one stable named swarm conversation."""
    return swarm.send(name, text)






@mcp.tool
def swarm_lane_start(
    task_id: str,
    worker_name: str,
    canonical_root: str,
    resources: list,
    lease_seconds: int = 900,
    run_id: str = "",
    parent_run_id: str = "",
):
    """Create one isolated MVCC development lane after verifying the task lease and hierarchical resource claims."""
    return lanes.start(
        task_id=task_id,
        worker_name=worker_name,
        canonical_root=canonical_root,
        resources=resources,
        lease_seconds=lease_seconds,
        run_id=run_id,
        parent_run_id=parent_run_id,
    )


@mcp.tool
def swarm_lane_get(lane_id: str):
    """Read one MVCC lane with its run and currently-live resource claims."""
    return lanes.get(lane_id)


@mcp.tool
def swarm_lane_list(status: str = ""):
    """List MVCC lanes, optionally filtered by status."""
    return lanes.list(status=status)


@mcp.tool
def swarm_run_list(status: str = ""):
    """List durable MVCC runs, optionally filtered by status."""
    return lanes.runs(status=status)


@mcp.tool
def swarm_resource_claim(
    run_id: str,
    resource: str,
    mode: str = "write",
    lease_seconds: int = 900,
):
    """Add hierarchical read/write/scratch claim; scratch may mutate only inside the lane and is never published."""
    return lanes.claim_resource(
        run_id=run_id,
        resource=resource,
        mode=mode,
        lease_seconds=lease_seconds,
    )


@mcp.tool
def swarm_resource_claim_list(status: str = ""):
    """List durable resource claims and their ownership/lease status."""
    return lanes.claims(status=status)


@mcp.tool
def swarm_lane_heartbeat(lane_id: str, lease_seconds: int = 900):
    """Renew all currently-live resource claims belonging to one MVCC lane."""
    return lanes.heartbeat(lane_id, lease_seconds=lease_seconds)


@mcp.tool
def swarm_lane_commit(lane_id: str):
    """Validate read/write sets against canonical base hashes and publish the whole lane or return conflict without overwrite."""
    return lanes.commit(lane_id)


@mcp.tool
def swarm_lane_recover():
    """Recover any crash-interrupted MVCC commit by rolling canonical back from its pre-publish journal."""
    return lanes.recover_incomplete_commits()


@mcp.tool
def swarm_lane_fork(lane_id: str, lease_seconds: int = 900):
    """Fork a conflicted/aborted lane from the latest canonical workspace as a new run/lane."""
    return lanes.fork(lane_id, lease_seconds=lease_seconds)


@mcp.tool
def swarm_lane_abort(
    lane_id: str,
    reason: str = "aborted by worker",
    cleanup: bool = False,
):
    """Abort a lane, release its resource claims, and optionally remove the private workspace."""
    return lanes.abort(lane_id, reason=reason, cleanup=cleanup)


@mcp.tool
def swarm_commit_list(status: str = ""):
    """List durable MVCC commit attempts including conflicts and committed writes."""
    return lanes.commits(status=status)


@mcp.tool
def swarm_graph_create(
    name: str,
    description: str = "",
    graph_id: str = "",
    metadata: dict = None,
):
    """Create one durable draft SwarmGraph. Populate it, validate it, then activate it."""
    return store.graph_create(
        name,
        description=description,
        graph_id=graph_id,
        metadata=metadata,
    )


@mcp.tool
def swarm_graph_get(graph_id: str = ""):
    """Read one full SwarmGraph; omit graph_id to read the active graph."""
    return store.graph_get(graph_id)


@mcp.tool
def swarm_graph_list():
    """List durable SwarmGraphs with revision, counts and active marker."""
    return store.graphs()


@mcp.tool
def swarm_graph_activate(graph_id: str, expected_revision: int = -1):
    """Validate and activate a SwarmGraph as an execution gate for task dispatch."""
    return store.graph_activate(graph_id, expected_revision=expected_revision)


@mcp.tool
def swarm_graph_deactivate(graph_id: str = ""):
    """Deactivate the current SwarmGraph and fall back to the legacy durable task DAG."""
    return store.graph_deactivate(graph_id)


@mcp.tool
def swarm_graph_update(graph_id: str, patch: dict, expected_revision: int = -1):
    """Update graph name/description/metadata using optional optimistic revision control."""
    return store.graph_update(
        graph_id,
        patch,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_replace(
    graph_id: str,
    nodes: list,
    edges: list,
    expected_revision: int = -1,
):
    """Atomically replace a graph topology after full normalization/validation."""
    return store.graph_replace(
        graph_id,
        nodes,
        edges,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_preview(graph_id: str = ""):
    """Compile a draft/active graph into effective edge states and task gates without mutation."""
    return store.graph_preview(graph_id)


@mcp.tool
def swarm_graph_delete(graph_id: str, expected_revision: int = -1):
    """Delete one SwarmGraph; deleting the active graph also deactivates graph routing."""
    return store.graph_delete(graph_id, expected_revision=expected_revision)


@mcp.tool
def swarm_graph_node_add(
    graph_id: str,
    node_id: str,
    kind: str = "task",
    task_id: str = "",
    role: str = "",
    label: str = "",
    status: str = "ready",
    state: object = None,
    metadata: dict = None,
    expected_revision: int = -1,
):
    """Add a graph node. Task nodes bind durable task_id; control/router/join nodes may use durable state."""
    return store.graph_node_add(
        graph_id,
        node_id,
        kind=kind,
        task_id=task_id,
        role=role,
        label=label,
        status=status,
        state=state,
        metadata=metadata,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_node_update(
    graph_id: str,
    node_id: str,
    patch: dict,
    expected_revision: int = -1,
):
    """Patch graph node kind/task/role/label/status/state/metadata with revision protection."""
    return store.graph_node_update(
        graph_id,
        node_id,
        patch,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_node_remove(
    graph_id: str,
    node_id: str,
    expected_revision: int = -1,
):
    """Remove a graph node and all incident edges."""
    return store.graph_node_remove(
        graph_id,
        node_id,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_edge_add(
    graph_id: str,
    source: str,
    target: str,
    kind: str = "dependency",
    condition: dict = None,
    edge_id: str = "",
    label: str = "",
    metadata: dict = None,
    expected_revision: int = -1,
):
    """Add dependency/conditional edge. Conditions are structured data, never executable code."""
    return store.graph_edge_add(
        graph_id,
        source,
        target,
        kind=kind,
        condition=condition,
        edge_id=edge_id,
        label=label,
        metadata=metadata,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_edge_update(
    graph_id: str,
    edge_id: str,
    patch: dict,
    expected_revision: int = -1,
):
    """Patch source/target/kind/condition/label/metadata on one edge."""
    return store.graph_edge_update(
        graph_id,
        edge_id,
        patch,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_edge_remove(
    graph_id: str,
    edge_id: str,
    expected_revision: int = -1,
):
    """Remove one explicit SwarmGraph edge."""
    return store.graph_edge_remove(
        graph_id,
        edge_id,
        expected_revision=expected_revision,
    )


@mcp.tool
def swarm_graph_validate(graph_id: str = ""):
    """Validate topology/task bindings and report entry/terminal nodes; omit id for active graph."""
    return store.graph_validate(graph_id)


@mcp.tool
def swarm_task_create(
    title: str,
    description: str = "",
    owner: str = "",
    dependencies: list = None,
    payload: object = None,
    group_id: str = "",
    parent_task_id: str = "",
    max_attempts: int = 3,
    assist_parent_task_id: str = "",
    assist_kind: str = "",
    assist_write_policy: str = "",
    resource_hints: list = None,
    canonical_root: str = "",
    preferred_role: str = "",
    required_capabilities: list = None,
    read_resources: list = None,
    write_resources: list = None,
    exclusive_resources: list = None,
    parallelizable: bool = True,
    shard_strategy: str = "",
):
    """Create a durable task with capability, resource-intent, dependency and retry metadata."""
    return store.task_create(
        title,
        description,
        owner,
        dependencies=dependencies,
        payload=payload,
        group_id=group_id,
        parent_task_id=parent_task_id,
        max_attempts=max_attempts,
        assist_parent_task_id=assist_parent_task_id,
        assist_kind=assist_kind,
        assist_write_policy=assist_write_policy,
        resource_hints=resource_hints,
        canonical_root=canonical_root,
        preferred_role=preferred_role,
        required_capabilities=required_capabilities,
        read_resources=read_resources,
        write_resources=write_resources,
        exclusive_resources=exclusive_resources,
        parallelizable=parallelizable,
        shard_strategy=shard_strategy,
    )


@mcp.tool
def swarm_task_assist(
    parent_task_id: str,
    title: str,
    description: str = "",
    owner: str = "",
    assist_kind: str = "readonly_analysis",
    write_policy: str = "",
    resource_hints: list = None,
    canonical_root: str = "",
    payload: object = None,
    max_attempts: int = 2,
):
    """Create a JIT sidecar that runs in parallel with its primary task."""
    return store.task_assist(
        parent_task_id=parent_task_id,
        title=title,
        description=description,
        owner=owner,
        assist_kind=assist_kind,
        write_policy=write_policy,
        resource_hints=resource_hints,
        canonical_root=canonical_root,
        payload=payload,
        max_attempts=max_attempts,
    )


@mcp.tool
def swarm_task_assist_list(parent_task_id: str, include_terminal: bool = True):
    """List sidecar tasks attached to one primary task."""
    return store.task_assists(parent_task_id, include_terminal=include_terminal)


@mcp.tool
def swarm_task_step_fingerprint(
    task_id: str,
    step_key: str,
    inputs: object = None,
    resource_paths: list = None,
):
    """Hash step inputs together with current MVCC resource versions."""
    return store.task_step_fingerprint(
        task_id,
        step_key,
        inputs=inputs,
        resource_paths=resource_paths,
    )


@mcp.tool
def swarm_task_step_acquire(
    task_id: str,
    step_key: str,
    worker_name: str,
    input_fingerprint: str = "",
    lease_seconds: int = 900,
):
    """Acquire or reuse an idempotent durable step inside a task."""
    return store.task_step_acquire(
        task_id,
        step_key,
        worker_name,
        input_fingerprint=input_fingerprint,
        lease_seconds=lease_seconds,
    )


@mcp.tool
def swarm_task_step_heartbeat(
    task_id: str,
    step_key: str,
    worker_name: str,
    lease_seconds: int = 900,
):
    """Renew one active durable task-step lease."""
    return store.task_step_heartbeat(
        task_id,
        step_key,
        worker_name,
        lease_seconds=lease_seconds,
    )


@mcp.tool
def swarm_task_step_complete(
    task_id: str,
    step_key: str,
    worker_name: str,
    status: str = "done",
    result: object = None,
    summary: str = "",
    error: str = "",
):
    """Complete/fail one durable task step so future resumes can reuse it."""
    return store.task_step_complete(
        task_id,
        step_key,
        worker_name,
        status=status,
        result=result,
        summary=summary,
        error=error,
    )


@mcp.tool
def swarm_task_step_list(task_id: str):
    """List durable step results for one task."""
    return {"task_id": task_id, "steps": store.task_steps(task_id)}


@mcp.tool
def swarm_task_claim(
    task_id: str,
    worker_name: str,
    lease_seconds: int = 900,
    superstep_id: str = "",
):
    """Atomically lease one task, optionally binding it to an active superstep."""
    return store.task_claim(
        task_id,
        worker_name,
        lease_seconds,
        superstep_id=superstep_id,
    )


@mcp.tool
def swarm_task_heartbeat(task_id: str, worker_name: str, lease_seconds: int = 900):
    """Renew one active task lease."""
    return store.task_heartbeat(task_id, worker_name, lease_seconds)


@mcp.tool
def swarm_task_release(
    task_id: str,
    worker_name: str,
    status: str = "done",
    summary: str = "",
    result: object = None,
    error: str = "",
    retryable: bool = True,
    retry_delay_seconds: float = 0.0,
):
    """Release a lease, publish a result, or schedule a bounded retry."""
    return store.task_release(
        task_id,
        worker_name,
        status=status,
        summary=summary,
        result=result,
        error=error,
        retryable=retryable,
        retry_delay_seconds=retry_delay_seconds,
    )


@mcp.tool
def swarm_task_fanout(
    items: list,
    owner: str = "",
    title_prefix: str = "Shard",
    dependencies: list = None,
    group_id: str = "",
    max_attempts: int = 3,
    preferred_role: str = "",
    required_capabilities: list = None,
    read_resources: list = None,
    write_resources: list = None,
    exclusive_resources: list = None,
    canonical_root: str = "",
    shard_strategy: str = "map",
):
    """Create capability-aware Send-style map shards sharing one reducer group."""
    return store.task_fanout(
        items,
        owner,
        title_prefix=title_prefix,
        dependencies=dependencies,
        group_id=group_id,
        max_attempts=max_attempts,
        preferred_role=preferred_role,
        required_capabilities=required_capabilities,
        read_resources=read_resources,
        write_resources=write_resources,
        exclusive_resources=exclusive_resources,
        canonical_root=canonical_root,
        shard_strategy=shard_strategy,
    )


@mcp.tool
def swarm_task_reduce(group_id: str):
    """Aggregate completed shard results for one fan-out group."""
    return store.task_reduce(group_id)


@mcp.tool
def swarm_superstep_begin(
    participants: list,
    step_id: str = "",
    metadata: dict = None,
):
    """Begin an all-or-nothing superstep barrier for exact worker instances."""
    return store.superstep_begin(participants, step_id=step_id, metadata=metadata)


@mcp.tool
def swarm_superstep_stage(step_id: str, worker_name: str, writes: list):
    """Stage pending writes for one superstep participant without publishing them."""
    return store.superstep_stage(step_id, worker_name, writes)


@mcp.tool
def swarm_superstep_fail(step_id: str, worker_name: str, error: str):
    """Abort a superstep because one participant failed; pending writes stay uncommitted."""
    return store.superstep_fail(step_id, worker_name, error)


@mcp.tool
def swarm_superstep_abort(step_id: str, reason: str = "aborted by orchestrator"):
    """Explicitly abort an open superstep without publishing pending writes."""
    return store.superstep_abort(step_id, reason)


@mcp.tool
def swarm_superstep_commit(step_id: str):
    """Atomically publish all staged writes after the full participant barrier is ready."""
    return store.superstep_commit(step_id)


@mcp.tool
def swarm_superstep_get(step_id: str, include_pending: bool = True):
    """Read one superstep barrier and optionally its pending writes."""
    return store.superstep_get(step_id, include_pending=include_pending)


@mcp.tool
def swarm_superstep_list(include_pending: bool = False):
    """List durable superstep barriers."""
    return store.supersteps(include_pending=include_pending)


@mcp.tool
def swarm_interrupt_create(
    prompt: str,
    task_id: str = "",
    payload: object = None,
    interrupt_id: str = "",
):
    """Persist a human-input interrupt and pause its task without relying on chat memory."""
    return store.interrupt_create(
        prompt,
        task_id=task_id,
        payload=payload,
        interrupt_id=interrupt_id,
    )


@mcp.tool
def swarm_interrupt_resume(interrupt_id: str, value: object = None):
    """Resume one durable human-input interrupt and make its task runnable again."""
    return store.interrupt_resume(interrupt_id, value=value)


@mcp.tool
def swarm_interrupt_list(status: str = ""):
    """List durable interrupts, optionally filtered to open/resumed."""
    return store.interrupts(status=status)


@mcp.tool
def swarm_checkpoint_create(
    label: str = "",
    metadata: dict = None,
    retain: int = 20,
    parent_checkpoint_id: str = "",
):
    """Create a bounded coordination checkpoint with explicit ancestry."""
    return store.checkpoint_create(
        label=label,
        metadata=metadata,
        retain=retain,
        parent_checkpoint_id=parent_checkpoint_id,
    )


@mcp.tool
def swarm_checkpoint_list():
    """List coordination checkpoints without returning their full state snapshots."""
    return store.checkpoints()


@mcp.tool
def swarm_checkpoint_lineage(checkpoint_id: str = ""):
    """Return checkpoint ancestry from one checkpoint (or the current head) backwards."""
    return store.checkpoint_lineage(checkpoint_id)


@mcp.tool
def swarm_checkpoint_restore(checkpoint_id: str):
    """Safely restore coordination state when no live lease or open superstep exists."""
    return store.checkpoint_restore(checkpoint_id)


@mcp.tool
def swarm_task_update(task_id: str, status: str = "", owner: str = "", summary: str = ""):
    """Update durable task status/owner/summary."""
    return store.task_update(task_id, status, owner, summary)


@mcp.tool
def swarm_task_list():
    """List durable shared tasks."""
    return store.tasks()


@mcp.tool
def swarm_status_set(key: str, value: str):
    """Set a small shared status value."""
    return store.status_set(key, value)


@mcp.tool
def swarm_status_get():
    """Read shared status values."""
    return store.status()


if __name__ == "__main__":
    mcp.run()
