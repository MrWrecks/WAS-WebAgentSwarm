"""Optional FastMCP server for Web Agent Swarm."""

try:
    from fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit(
        'FastMCP is not installed. Run: pip install -e ".[mcp]"'
    ) from exc

from .bridge import BridgeClient
from .state import DurableStore
from .swarm import SwarmRuntime


mcp = FastMCP("Web Agent Swarm")
bridge = BridgeClient()
store = DurableStore()
swarm = SwarmRuntime(bridge=bridge, store=store)


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
    roles: list,
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
    max_new_roles: int = 2,
    max_instances_per_role: int = 4,
):
    """Return task routing, shard frontier, leases, reducers and instance pressure."""
    return swarm.coordination_plan(
        active_roles=active_roles,
        max_new_roles=max_new_roles,
        max_instances_per_role=max_instances_per_role,
    )


@mcp.tool
def web_agent_swarm_elastic_plan(
    max_new_roles: int = 2,
    max_instances_per_role: int = 4,
):
    """Recommend missing-role and same-role instances from durable shard pressure."""
    return swarm.elastic_plan(
        max_new_roles=max_new_roles,
        max_instances_per_role=max_instances_per_role,
    )


@mcp.tool
def web_agent_swarm_scale(
    requested_roles: list = None,
    requested_instances: dict = None,
    worker_count: int = 0,
    max_new_roles: int = 2,
    max_instances_per_role: int = 4,
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
def swarm_task_create(
    title: str,
    description: str = "",
    owner: str = "",
    dependencies: list = None,
    payload: object = None,
    group_id: str = "",
    parent_task_id: str = "",
    max_attempts: int = 3,
):
    """Create a durable task with dependency, payload, grouping and retry metadata."""
    return store.task_create(
        title,
        description,
        owner,
        dependencies=dependencies,
        payload=payload,
        group_id=group_id,
        parent_task_id=parent_task_id,
        max_attempts=max_attempts,
    )


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
    owner: str,
    title_prefix: str = "Shard",
    dependencies: list = None,
    group_id: str = "",
    max_attempts: int = 3,
):
    """Create Send-style map shards sharing one reducer group."""
    return store.task_fanout(
        items,
        owner,
        title_prefix=title_prefix,
        dependencies=dependencies,
        group_id=group_id,
        max_attempts=max_attempts,
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
