"""FastMCP wrapper for bundled CodexPro Lite."""

try:
    from fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit('FastMCP is not installed. Run: pip install -e ".[mcp]"') from exc

from .codexpro_core import CodexProLiteRuntime


runtime = CodexProLiteRuntime()
mcp = FastMCP("CodexPro Lite")


@mcp.tool
def codexpro(action: str = "list_actions", args: dict = None):
    return runtime.codexpro(action, args)


@mcp.tool
def server_config():
    return runtime.server_config()


@mcp.tool
def codexpro_self_test(
    workspace_id: str = "",
    write_probe: bool = True,
    bash_probe: bool = True,
    pro_context_probe: bool = True,
):
    return runtime.codexpro_self_test(
        workspace_id=workspace_id,
        write_probe=write_probe,
        bash_probe=bash_probe,
        pro_context_probe=pro_context_probe,
    )


@mcp.tool
def load_skill(
    name: str,
    workspace_id: str = "",
    max_bytes: int = 40000,
):
    return runtime.load_skill(
        name=name,
        workspace_id=workspace_id,
        max_bytes=max_bytes,
    )


@mcp.tool
def open_current_workspace(
    include_tree: bool = False,
    max_depth: int = 2,
):
    return runtime.open_current_workspace(
        include_tree=include_tree,
        max_depth=max_depth,
    )


@mcp.tool
def open_workspace(
    root: str = "",
    path: str = "",
    include_tree: bool = True,
    max_depth: int = 3,
):
    return runtime.open_workspace(
        root=root,
        path=path,
        include_tree=include_tree,
        max_depth=max_depth,
    )


@mcp.tool
def inspect_workspace(
    workspace_id: str = "",
    path: str = ".",
    max_files: int = 300,
):
    return runtime.inspect_workspace(
        workspace_id=workspace_id,
        path=path,
        max_files=max_files,
    )


@mcp.tool
def tree(
    workspace_id: str = "",
    path: str = ".",
    max_depth: int = 4,
    include_hidden: bool = False,
    max_entries: int = 800,
):
    return runtime.tree(
        workspace_id=workspace_id,
        path=path,
        max_depth=max_depth,
        include_hidden=include_hidden,
        max_entries=max_entries,
    )


@mcp.tool
def search(
    query: str,
    workspace_id: str = "",
    regex: bool = False,
    path: str = ".",
    glob: str = "",
    include_hidden: bool = False,
    max_results: int = 200,
):
    return runtime.search(
        query=query,
        workspace_id=workspace_id,
        regex=regex,
        path=path,
        glob=glob,
        include_hidden=include_hidden,
        max_results=max_results,
    )


@mcp.tool
def read(
    path: str,
    workspace_id: str = "",
    start_line: int = 1,
    end_line: int = 0,
    max_bytes: int = 300000,
):
    return runtime.read(
        path=path,
        workspace_id=workspace_id,
        start_line=start_line,
        end_line=end_line,
        max_bytes=max_bytes,
    )


@mcp.tool
def write(
    path: str,
    content: str,
    workspace_id: str = "",
    create_dirs: bool = True,
    overwrite: bool = True,
):
    return runtime.write(
        path=path,
        content=content,
        workspace_id=workspace_id,
        create_dirs=create_dirs,
        overwrite=overwrite,
    )


@mcp.tool
def edit(
    path: str,
    old_text: str,
    new_text: str,
    workspace_id: str = "",
    replace_all: bool = False,
    expected_replacements: int = 0,
):
    return runtime.edit(
        path=path,
        old_text=old_text,
        new_text=new_text,
        workspace_id=workspace_id,
        replace_all=replace_all,
        expected_replacements=expected_replacements,
    )


@mcp.tool
def apply_patch(
    patch: str,
    workspace_id: str = "",
):
    return runtime.apply_patch(
        patch=patch,
        workspace_id=workspace_id,
    )


@mcp.tool
def bash(
    command: str,
    workspace_id: str = "",
    cwd: str = ".",
    timeout_ms: int = 30000,
):
    return runtime.bash(
        command=command,
        workspace_id=workspace_id,
        cwd=cwd,
        timeout_ms=timeout_ms,
    )


@mcp.tool
def run_background(
    command: str,
    workspace_id: str = "",
    cwd: str = ".",
    label: str = "",
    timeout_ms: int = 21600000,
):
    return runtime.run_background(
        command=command,
        workspace_id=workspace_id,
        cwd=cwd,
        label=label,
        timeout_ms=timeout_ms,
    )


@mcp.tool
def background_status(
    task_id: str,
    since_cursor: int = 0,
    wait_ms: int = 0,
):
    return runtime.background_status(
        task_id=task_id,
        since_cursor=since_cursor,
        wait_ms=wait_ms,
    )


@mcp.tool
def background_cancel(task_id: str):
    return runtime.background_cancel(task_id=task_id)


@mcp.tool
def show_changes(
    workspace_id: str = "",
    path: str = "",
    staged: bool = False,
    include_diff: bool = True,
):
    return runtime.show_changes(
        workspace_id=workspace_id,
        path=path,
        staged=staged,
        include_diff=include_diff,
    )


@mcp.tool
def read_handoff(workspace_id: str = ""):
    return runtime.read_handoff(workspace_id=workspace_id)


@mcp.tool
def wait_for_handoff(
    workspace_id: str = "",
    max_wait_seconds: int = 20,
    poll_ms: int = 1000,
):
    return runtime.wait_for_handoff(
        workspace_id=workspace_id,
        max_wait_seconds=max_wait_seconds,
        poll_ms=poll_ms,
    )


@mcp.tool
def compact_context(
    workspace_id: str = "",
    summary: str = "",
    decisions: list = None,
    active_files: list = None,
    next_steps: list = None,
    include_git: bool = True,
    max_bytes: int = 12000,
):
    return runtime.compact_context(
        workspace_id=workspace_id,
        summary=summary,
        decisions=decisions,
        active_files=active_files,
        next_steps=next_steps,
        include_git=include_git,
        max_bytes=max_bytes,
    )


@mcp.tool
def resume_context(
    workspace_id: str = "",
    max_bytes: int = 12000,
):
    return runtime.resume_context(
        workspace_id=workspace_id,
        max_bytes=max_bytes,
    )


@mcp.tool
def export_pro_context(
    workspace_id: str = "",
    title: str = "Repository context",
    selected_paths: list = None,
    include_important_files: bool = True,
    include_changed_files: bool = True,
    include_diff: bool = True,
    include_ai_bridge: bool = True,
    max_depth: int = 3,
    max_files: int = 24,
    max_file_bytes: int = 60000,
    max_total_bytes: int = 400000,
):
    return runtime.export_pro_context(
        workspace_id=workspace_id,
        title=title,
        selected_paths=selected_paths,
        include_important_files=include_important_files,
        include_changed_files=include_changed_files,
        include_diff=include_diff,
        include_ai_bridge=include_ai_bridge,
        max_depth=max_depth,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )


@mcp.tool
def handoff_to_agent(
    plan: str,
    workspace_id: str = "",
    agent: str = "custom",
    agent_name: str = "",
    model: str = "",
    title: str = "",
    append: bool = False,
):
    return runtime.handoff_to_agent(
        plan=plan,
        workspace_id=workspace_id,
        agent=agent,
        agent_name=agent_name,
        model=model,
        title=title,
        append=append,
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
