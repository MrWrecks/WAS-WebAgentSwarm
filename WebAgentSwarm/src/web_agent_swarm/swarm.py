"""Browser-hosted swarm runtime."""

import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional

from .bridge import BridgeClient
from .roles import DEFAULT_ROLES, plan_roles
from .state import DurableStore


_CONVERSATION_RE = re.compile(r"/c/([^/?#]+)")


def conversation_id(url: str) -> str:
    match = _CONVERSATION_RE.search(str(url or ""))
    return match.group(1) if match else ""


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
        result = plan_roles(
            self.store.tasks(),
            worker_count=worker_count,
            requested_roles=requested_roles,
        )
        if not result.get("ok"):
            return result
        roles = list(result["roles"])
        result.update({
            "phase": "proposal",
            "requires_confirmation": True,
            "total_web_agents": len(roles) + 1,
            "includes_main": True,
            "role_plan": [
                {"role": role, "responsibility": DEFAULT_ROLES[role]}
                for role in roles
            ],
            "confirmation_prompt": (
                "Proposed swarm: {0} worker(s) + 1 main agent: {1}. "
                "Confirm, change the count, or provide a different role subset."
            ).format(len(roles), ", ".join(roles)),
        })
        return result

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
    def _worker_prompt(role: str) -> str:
        return (
            "You are a Web Agent worker. Role: {0}. Responsibility: {1} "
            "Use the shared swarm task/status tools as durable coordination state. "
            "Do not treat chat text alone as proof that external work is complete. "
            "If a CodexPro-style local development MCP is connected, use it for concrete "
            "workspace inspection, edits, verification, background runs, handoff, and context "
            "management as appropriate. Start by reading the shared tasks and status, then "
            "work on the highest-priority item owned by your role or wait for the main agent."
        ).format(role, DEFAULT_ROLES[role])

    @staticmethod
    def _main_prompt(roles: List[str]) -> str:
        return (
            "You are the main Web Agent orchestrator. Active worker roles: {0}. "
            "Read web_agent_swarm_snapshot and the shared task/status board. "
            "Do not interrupt workers that are generating. When a worker is idle, decide "
            "whether durable state warrants a next instruction and send it with "
            "web_agent_swarm_send. Avoid duplicate instructions by checking the worker's "
            "last visible user message. If CodexPro-style local development tools are "
            "available, use them for concrete repository work rather than guessing."
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

    def start(
        self,
        roles: List[str],
        spawn_url: str = "",
        start_watch: bool = True,
        interval: float = 15.0,
        max_silence: float = 300.0,
    ) -> Dict[str, object]:
        selected = []
        for role in roles:
            clean = str(role or "").strip().lower()
            if clean not in DEFAULT_ROLES:
                return {"ok": False, "error": "unknown role: {0}".format(clean)}
            if clean not in selected:
                selected.append(clean)
        if not selected:
            return {"ok": False, "error": "at least one worker role is required"}

        current = self.store.bindings()
        for stale in list(current):
            if stale == "main":
                continue
            if stale not in selected:
                self.store.unbind(stale)

        tabs = self._tabs()
        target = str(spawn_url or "").strip() or self._spawn_url(tabs)
        if not target.startswith("https://chatgpt.com/"):
            return {"ok": False, "error": "spawn_url must be a chatgpt.com URL"}

        desired = [("main", "main", "")] + [
            (role, "worker", role) for role in selected
        ]
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
                    else self._worker_prompt(role)
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
                    "Continue as the {0} worker. Re-read the shared task/status state before "
                    "taking the next action; do not repeat completed work."
                ).format(role)
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
        return {
            "ok": all(item.get("ok") for item in records) and watcher.get("ok", False),
            "worker_roles": selected,
            "worker_count": len(selected),
            "total_web_agents": len(selected) + 1,
            "agents": records,
            "watcher": watcher,
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
            "completion_truth": (
                "Browser idle means only that the page is not generating. "
                "Use durable task state and real external verification for completion."
            ),
        }
