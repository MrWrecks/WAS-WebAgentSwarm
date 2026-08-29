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
    spawn_url: str = "",
    start_watch: bool = True,
    interval: float = 15.0,
    max_silence: float = 300.0,
):
    """Start the confirmed swarm and, by default, its event-aware watcher."""
    return swarm.start(
        roles,
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
def web_agent_swarm_send(name: str, text: str):
    """Send to one stable named swarm conversation."""
    return swarm.send(name, text)


@mcp.tool
def swarm_task_create(title: str, description: str = "", owner: str = ""):
    """Create a durable shared task."""
    return store.task_create(title, description, owner)


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
