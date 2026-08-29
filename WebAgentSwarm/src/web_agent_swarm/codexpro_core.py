"""Clean-room CodexPro-compatible local development runtime."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional


BLOCKED = {
    ".git", "node_modules", ".env", ".ssh", "dist", "build",
    ".next", "coverage", ".cache", "__pycache__",
}
SAFE_COMMANDS = {
    "python", "python3", "pytest", "node", "npm", "npx", "pnpm",
    "yarn", "cargo", "go", "make", "cmake", "ctest", "ruff",
    "mypy", "pyright", "tsc", "eslint",
}
ACTIONS = [
    "server_config", "codexpro_self_test", "load_skill",
    "open_current_workspace", "open_workspace", "inspect_workspace",
    "tree", "search", "read", "write", "edit", "apply_patch",
    "bash", "run_background", "background_status", "background_cancel",
    "show_changes", "read_handoff", "wait_for_handoff",
    "compact_context", "resume_context", "export_pro_context",
    "handoff_to_agent",
]


class CodexProLiteRuntime:
    def __init__(self, default_root: Optional[str] = None) -> None:
        self.default_root = Path(
            default_root or os.environ.get("CODEXPRO_ROOT") or os.getcwd()
        ).expanduser().resolve()
        self.workspaces: Dict[str, Path] = {}
        self.current_workspace_id: Optional[str] = None
        self.background: Dict[str, Dict[str, object]] = {}

    @staticmethod
    def _wid(root: Path) -> str:
        return "ws_" + hashlib.sha256(str(root).encode()).hexdigest()[:16]

    @staticmethod
    def _blocked(relative: Path) -> bool:
        return any(part in BLOCKED for part in relative.parts)

    def _register(self, root: Path) -> str:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError("workspace root does not exist")
        wid = self._wid(root)
        self.workspaces[wid] = root
        self.current_workspace_id = wid
        return wid

    def _root(self, workspace_id: str = "") -> Path:
        wid = workspace_id or self.current_workspace_id
        if not wid:
            wid = self._register(self.default_root)
        if wid not in self.workspaces:
            raise ValueError("unknown workspace_id")
        return self.workspaces[wid]

    def _path(
        self,
        workspace_id: str = "",
        path: str = ".",
        must_exist: bool = False,
    ) -> Path:
        root = self._root(workspace_id)
        target = (root / path).resolve()
        try:
            rel = target.relative_to(root)
        except ValueError as exc:
            raise ValueError("path escapes workspace") from exc
        if self._blocked(rel):
            raise ValueError("path is blocked")
        if must_exist and not target.exists():
            raise FileNotFoundError(path)
        return target

    @staticmethod
    def _git(root: Path, args: List[str], timeout: int = 15):
        if not shutil.which("git"):
            return None
        return subprocess.run(
            ["git"] + args,
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def server_config(self) -> Dict[str, object]:
        return {
            "name": "CodexPro Lite",
            "implementation": "clean-room open-source compatibility server",
            "writeMode": "workspace",
            "bashMode": "allowlisted",
            "contextDir": ".ai-bridge",
            "blockedParts": sorted(BLOCKED),
            "safeCommands": sorted(SAFE_COMMANDS),
            "authEnabled": False,
        }

    def open_current_workspace(
        self,
        include_tree: bool = False,
        max_depth: int = 2,
        **kwargs,
    ):
        return self.open_workspace(
            root=str(self.default_root),
            include_tree=include_tree,
            max_depth=max_depth,
        )

    def open_workspace(
        self,
        root: str = "",
        path: str = "",
        include_tree: bool = True,
        max_depth: int = 3,
        **kwargs,
    ):
        target = Path(root or path or self.default_root).expanduser().resolve()
        wid = self._register(target)
        git = self._git(target, ["status", "--short", "--branch"])
        result = {
            "workspace_id": wid,
            "root": str(target),
            "write_mode": "workspace",
            "agents_loaded": (target / "AGENTS.md").is_file(),
            "git_status": (
                git.stdout.strip() if git and git.returncode == 0
                else "git unavailable or not a repository"
            ),
        }
        if result["agents_loaded"]:
            result["agents_path"] = "AGENTS.md"
        if include_tree:
            result["tree"] = self.tree(
                workspace_id=wid, max_depth=max_depth
            )["text"]
        return result

    def tree(
        self,
        workspace_id: str = "",
        path: str = ".",
        max_depth: int = 4,
        include_hidden: bool = False,
        max_entries: int = 800,
        **kwargs,
    ):
        root = self._root(workspace_id)
        base = self._path(workspace_id, path, True)
        lines = [str(base.relative_to(root)) if base != root else "."]
        count = 0

        def walk(folder: Path, prefix: str, depth: int) -> None:
            nonlocal count
            if depth >= max_depth or count >= max_entries:
                return
            try:
                children = list(folder.iterdir())
            except OSError:
                return
            visible = []
            for child in children:
                rel = child.relative_to(root)
                if self._blocked(rel):
                    continue
                if not include_hidden and child.name.startswith("."):
                    continue
                visible.append(child)
            visible.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
            for index, child in enumerate(visible):
                if count >= max_entries:
                    return
                count += 1
                last = index == len(visible) - 1
                lines.append(
                    prefix
                    + ("└── " if last else "├── ")
                    + child.name
                    + ("/" if child.is_dir() else "")
                )
                if child.is_dir():
                    walk(
                        child,
                        prefix + ("    " if last else "│   "),
                        depth + 1,
                    )

        if base.is_dir():
            walk(base, "", 0)
        return {
            "text": "\n".join(lines),
            "entries": count,
            "truncated": count >= max_entries,
        }

    def inspect_workspace(
        self,
        workspace_id: str = "",
        path: str = ".",
        max_files: int = 300,
        **kwargs,
    ):
        root = self._root(workspace_id)
        base = self._path(workspace_id, path, True)
        candidates = [base] if base.is_file() else base.rglob("*")
        files = []
        extensions: Dict[str, int] = {}
        entrypoints = []
        for item in candidates:
            if len(files) >= max_files:
                break
            if not item.is_file():
                continue
            rel = item.relative_to(root)
            if self._blocked(rel):
                continue
            files.append(str(rel))
            ext = item.suffix.lower() or "<none>"
            extensions[ext] = extensions.get(ext, 0) + 1
            if item.name in {
                "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
                "main.py", "server.py", "app.py", "index.js", "index.ts",
            }:
                entrypoints.append(str(rel))
        return {
            "root": str(root),
            "file_count": len(files),
            "languages_by_extension": extensions,
            "entrypoints": entrypoints,
            "coverage_warning": (
                "CodexPro Lite provides bounded file inventory; "
                "deep symbol/relationship indexing is not included."
            ),
        }

    def search(
        self,
        query: str,
        workspace_id: str = "",
        regex: bool = False,
        path: str = ".",
        glob: str = "",
        include_hidden: bool = False,
        max_results: int = 200,
        **kwargs,
    ):
        root = self._root(workspace_id)
        base = self._path(workspace_id, path, True)
        pattern = re.compile(query) if regex else None
        candidates = [base] if base.is_file() else base.rglob("*")
        matches = []
        for item in candidates:
            if len(matches) >= max_results:
                break
            if not item.is_file():
                continue
            rel = item.relative_to(root)
            if self._blocked(rel):
                continue
            if not include_hidden and any(p.startswith(".") for p in rel.parts):
                continue
            normalized = str(rel).replace("\\", "/")
            if glob and not fnmatch.fnmatch(normalized, glob):
                continue
            try:
                text = item.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                hit = bool(pattern.search(line)) if pattern else query in line
                if hit:
                    matches.append({
                        "path": str(rel),
                        "line": number,
                        "text": line[:1000],
                    })
                    if len(matches) >= max_results:
                        break
        return {"matches": matches, "truncated": len(matches) >= max_results}

    def read(
        self,
        path: str,
        workspace_id: str = "",
        start_line: int = 1,
        end_line: int = 0,
        max_bytes: int = 300000,
        **kwargs,
    ):
        target = self._path(workspace_id, path, True)
        if not target.is_file():
            raise ValueError("path is not a file")
        text = target.read_bytes()[:max_bytes].decode("utf-8")
        lines = text.splitlines()
        start = max(1, start_line)
        end = min(len(lines), end_line or len(lines))
        body = "\n".join(
            "{0:>4} | {1}".format(i, lines[i - 1])
            for i in range(start, end + 1)
        )
        return {
            "path": path,
            "startLine": start,
            "endLine": end,
            "totalLines": len(lines),
            "text": body,
        }

    def write(
        self,
        path: str,
        content: str,
        workspace_id: str = "",
        create_dirs: bool = True,
        overwrite: bool = True,
        **kwargs,
    ):
        target = self._path(workspace_id, path)
        if target.exists() and not overwrite:
            raise FileExistsError(path)
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        return {
            "path": path,
            "existed": existed,
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        workspace_id: str = "",
        replace_all: bool = False,
        expected_replacements: int = 0,
        **kwargs,
    ):
        target = self._path(workspace_id, path, True)
        text = target.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            raise ValueError("old_text not found")
        if not replace_all and count != 1:
            raise ValueError("old_text matched multiple times")
        replacements = count if replace_all else 1
        if expected_replacements and replacements != expected_replacements:
            raise ValueError("replacement count mismatch")
        target.write_text(
            text.replace(old_text, new_text, -1 if replace_all else 1),
            encoding="utf-8",
        )
        return {"path": path, "replacements": replacements}

    def apply_patch(self, patch: str, workspace_id: str = "", **kwargs):
        root = self._root(workspace_id)
        if not shutil.which("git"):
            return {"ok": False, "error": "git is required for apply_patch"}
        for line in patch.splitlines():
            if line.startswith("--- ") or line.startswith("+++ "):
                candidate = line[4:].strip().split("\t", 1)[0]
                if candidate == "/dev/null":
                    continue
                if candidate.startswith("a/") or candidate.startswith("b/"):
                    candidate = candidate[2:]
                p = Path(candidate)
                if p.is_absolute() or ".." in p.parts or self._blocked(p):
                    return {"ok": False, "error": "unsafe patch path"}
        with tempfile.NamedTemporaryFile(
            "w", suffix=".patch", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(patch)
            patch_file = handle.name
        try:
            check = subprocess.run(
                ["git", "apply", "--check", patch_file],
                cwd=str(root),
                text=True,
                capture_output=True,
                check=False,
            )
            if check.returncode:
                return {"ok": False, "error": check.stderr.strip()}
            result = subprocess.run(
                ["git", "apply", patch_file],
                cwd=str(root),
                text=True,
                capture_output=True,
                check=False,
            )
            return {
                "ok": result.returncode == 0,
                "error": result.stderr.strip() or None,
            }
        finally:
            try:
                os.unlink(patch_file)
            except OSError:
                pass

    @staticmethod
    def _argv(command: str) -> List[str]:
        if any(x in command for x in ["&&", "||", "|", ">", "<", ";", "\n"]):
            raise ValueError("shell chaining/redirection is blocked")
        argv = shlex.split(command, posix=os.name != "nt")
        if not argv:
            raise ValueError("empty command")
        name = Path(argv[0]).name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name not in SAFE_COMMANDS:
            raise ValueError("command is not allowlisted: " + name)
        return argv

    def bash(
        self,
        command: str,
        workspace_id: str = "",
        cwd: str = ".",
        timeout_ms: int = 30000,
        **kwargs,
    ):
        started = time.time()
        result = subprocess.run(
            self._argv(command),
            cwd=str(self._path(workspace_id, cwd, True)),
            text=True,
            capture_output=True,
            timeout=max(1, timeout_ms / 1000),
            check=False,
        )
        return {
            "command": command,
            "exitCode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "durationMs": int((time.time() - started) * 1000),
        }

    def run_background(
        self,
        command: str,
        workspace_id: str = "",
        cwd: str = ".",
        label: str = "",
        timeout_ms: int = 21600000,
        **kwargs,
    ):
        root = self._root(workspace_id)
        runtime_dir = root / ".ai-bridge" / "background"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        task_id = uuid.uuid4().hex
        stdout_path = runtime_dir / (task_id + ".stdout")
        stderr_path = runtime_dir / (task_id + ".stderr")
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            process = subprocess.Popen(
                self._argv(command),
                cwd=str(self._path(workspace_id, cwd, True)),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
            )
        self.background[task_id] = {
            "process": process,
            "stdout": stdout_path,
            "stderr": stderr_path,
            "label": label,
            "timeout_ms": timeout_ms,
        }
        return {"task_id": task_id, "status": "running", "label": label}

    def background_status(
        self,
        task_id: str,
        since_cursor: int = 0,
        wait_ms: int = 0,
        **kwargs,
    ):
        task = self.background.get(task_id)
        if not task:
            return {"ok": False, "error": "unknown task_id"}
        process = task["process"]
        if wait_ms and process.poll() is None:
            deadline = time.time() + min(wait_ms / 1000, 20)
            while time.time() < deadline and process.poll() is None:
                time.sleep(0.1)
        stdout = task["stdout"].read_text(
            encoding="utf-8", errors="replace"
        )
        stderr = task["stderr"].read_text(
            encoding="utf-8", errors="replace"
        )
        combined = stdout + ("\n" if stdout and stderr else "") + stderr
        cursor = max(0, since_cursor)
        return {
            "task_id": task_id,
            "status": "running" if process.poll() is None else "completed",
            "exitCode": process.poll(),
            "cursor": len(combined),
            "output": combined[cursor:],
        }

    def background_cancel(self, task_id: str, **kwargs):
        task = self.background.get(task_id)
        if not task:
            return {"ok": False, "error": "unknown task_id"}
        process = task["process"]
        if process.poll() is None:
            process.terminate()
        return {"ok": True, "task_id": task_id, "status": "cancelled"}

    def show_changes(
        self,
        workspace_id: str = "",
        path: str = "",
        staged: bool = False,
        include_diff: bool = True,
        **kwargs,
    ):
        root = self._root(workspace_id)
        status = self._git(root, ["status", "--short"])
        if status is None or status.returncode:
            return {"changed": False, "status": "git unavailable"}
        args = ["diff"]
        if staged:
            args.append("--cached")
        if path:
            args.extend(["--", path])
        diff = self._git(root, args)
        return {
            "changed": bool(status.stdout.strip()),
            "status": status.stdout.strip(),
            "diff": diff.stdout if include_diff and diff else "",
            "staged": staged,
        }

    def _context_dir(self, workspace_id: str = "") -> Path:
        path = self._root(workspace_id) / ".ai-bridge"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def handoff_to_agent(
        self,
        plan: str,
        workspace_id: str = "",
        agent: str = "custom",
        agent_name: str = "",
        model: str = "",
        title: str = "",
        append: bool = False,
        **kwargs,
    ):
        target = self._context_dir(workspace_id) / "current-plan.md"
        body = (
            "# " + (title or "Agent handoff") + "\n\n"
            + "- Agent: " + agent + "\n"
            + "- Agent name: " + agent_name + "\n"
            + "- Model: " + model + "\n\n"
            + "## Plan\n\n" + plan + "\n"
        )
        with target.open("a" if append else "w", encoding="utf-8") as handle:
            handle.write(body)
        return {"ok": True, "path": ".ai-bridge/current-plan.md"}

    def read_handoff(self, workspace_id: str = "", **kwargs):
        folder = self._context_dir(workspace_id)
        result = {}
        for name in [
            "current-plan.md", "handoff-run-state.json",
            "execution-log.jsonl", "loop-tests.txt",
        ]:
            path = folder / name
            if path.is_file():
                result[name] = path.read_text(
                    encoding="utf-8", errors="replace"
                )[:120000]
        return result

    def wait_for_handoff(
        self,
        workspace_id: str = "",
        max_wait_seconds: int = 20,
        poll_ms: int = 1000,
        **kwargs,
    ):
        state_path = self._context_dir(workspace_id) / "handoff-run-state.json"
        terminal = {"completed", "failed", "cancelled", "done", "error"}
        deadline = time.time() + min(max(1, max_wait_seconds), 60)
        state = {}
        while time.time() < deadline:
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except ValueError:
                    state = {"status": "invalid_json"}
                if str(state.get("status", "")).lower() in terminal:
                    break
            time.sleep(max(0.25, min(poll_ms / 1000, 5)))
        result = {
            "state": state,
            "terminal": str(state.get("status", "")).lower() in terminal,
        }
        result.update(self.read_handoff(workspace_id))
        return result

    def compact_context(
        self,
        workspace_id: str = "",
        summary: str = "",
        decisions: list = None,
        active_files: list = None,
        next_steps: list = None,
        include_git: bool = True,
        max_bytes: int = 12000,
        **kwargs,
    ):
        target = self._context_dir(workspace_id) / "web-context.md"
        sections = [
            "# Compact web context", "", "## Summary", summary or "(none)",
            "", "## Decisions",
        ]
        sections += ["- " + x for x in (decisions or [])]
        sections += ["", "## Active files"]
        sections += ["- " + x for x in (active_files or [])]
        sections += ["", "## Next steps"]
        sections += ["- " + x for x in (next_steps or [])]
        if include_git:
            sections += [
                "", "## Git",
                json.dumps(self.show_changes(workspace_id, include_diff=False)),
            ]
        raw = "\n".join(sections).encode("utf-8")[:max_bytes]
        target.write_bytes(raw)
        return {"ok": True, "path": ".ai-bridge/web-context.md"}

    def resume_context(
        self,
        workspace_id: str = "",
        max_bytes: int = 12000,
        **kwargs,
    ):
        target = self._context_dir(workspace_id) / "web-context.md"
        if not target.is_file():
            return {"ok": False, "error": "no compact context"}
        return {
            "ok": True,
            "text": target.read_bytes()[:max_bytes].decode(
                "utf-8", errors="replace"
            ),
        }

    def export_pro_context(
        self,
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
        **kwargs,
    ):
        root = self._root(workspace_id)
        selected = list(selected_paths or [])
        if include_important_files:
            for name in [
                "AGENTS.md", "README.md", "pyproject.toml", "package.json"
            ]:
                if (root / name).is_file() and name not in selected:
                    selected.append(name)
        sections = [
            "# " + title, "", "## Tree", self.tree(
                workspace_id, max_depth=max_depth
            )["text"],
        ]
        for rel in selected[:max_files]:
            try:
                target = self._path(workspace_id, rel, True)
                if target.is_file():
                    body = target.read_bytes()[:max_file_bytes].decode(
                        "utf-8", errors="replace"
                    )
                    sections += ["", "## " + rel, body]
            except Exception:
                pass
        if include_diff:
            sections += [
                "", "## Diff",
                self.show_changes(workspace_id).get("diff", ""),
            ]
        if include_ai_bridge:
            sections += [
                "", "## Handoff",
                json.dumps(
                    self.read_handoff(workspace_id),
                    ensure_ascii=False,
                    indent=2,
                ),
            ]
        raw = "\n".join(sections).encode("utf-8")[:max_total_bytes]
        target = self._context_dir(workspace_id) / "pro-context.md"
        target.write_bytes(raw)
        return {"ok": True, "path": ".ai-bridge/pro-context.md"}

    def load_skill(
        self,
        name: str,
        workspace_id: str = "",
        max_bytes: int = 40000,
        **kwargs,
    ):
        root = self._root(workspace_id)
        needle = name.strip().lower()
        for item in root.rglob("SKILL.md"):
            rel = item.relative_to(root)
            if self._blocked(rel):
                continue
            if needle in item.parent.name.lower() or needle in str(rel).lower():
                return {
                    "ok": True,
                    "name": name,
                    "path": str(rel),
                    "text": item.read_bytes()[:max_bytes].decode(
                        "utf-8", errors="replace"
                    ),
                }
        return {"ok": False, "error": "skill not found"}

    def codexpro_self_test(
        self,
        workspace_id: str = "",
        write_probe: bool = True,
        bash_probe: bool = True,
        pro_context_probe: bool = True,
        **kwargs,
    ):
        root = self._root(workspace_id)
        checks = {
            "workspace": root.is_dir(),
            "bash_policy": True,
            "write_probe": None,
            "pro_context_probe": None,
        }
        if bash_probe:
            try:
                self._argv("python --version")
            except Exception:
                checks["bash_policy"] = False
        if write_probe:
            target = self._context_dir(workspace_id) / "codexpro-self-test.md"
            target.write_text("CodexPro Lite self-test\n", encoding="utf-8")
            checks["write_probe"] = target.is_file()
        if pro_context_probe:
            checks["pro_context_probe"] = self.export_pro_context(
                workspace_id,
                max_files=2,
                max_total_bytes=20000,
            ).get("ok", False)
        return {
            "ok": all(v is not False for v in checks.values()),
            "checks": checks,
        }

    def codexpro(self, action: str = "list_actions", args: dict = None):
        if action in {"list_actions", "actions"}:
            return {"actions": ACTIONS, "action_count": len(ACTIONS)}
        if action not in ACTIONS:
            return {"ok": False, "error": "unknown action"}
        return getattr(self, action)(**dict(args or {}))
