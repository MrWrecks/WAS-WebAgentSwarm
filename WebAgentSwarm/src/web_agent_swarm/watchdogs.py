"""Small event/snapshot watchdogs for browser transport liveness.

Inspired by browser-use's separation between BrowserSession shared state and
private watchdog responsibilities. These detectors are pure: they do not mutate
durable task state or browser tabs.
"""

from typing import Dict, List


_CONVERSATION_LIMIT_MARKERS = (
    "你已达到此对话的长度上限",
    "达到此对话的长度上限",
    "开始新聊天以继续对话",
    "you've reached the maximum length for this conversation",
    "you have reached the maximum length for this conversation",
    "reached the maximum length for this conversation",
    "start a new chat to continue",
)


def conversation_limit_reached(ui: object) -> bool:
    if not isinstance(ui, dict):
        return False
    if bool(ui.get("conversationLimitReached")):
        return True
    texts = [
        str(ui.get("lastAssistantText") or ""),
        str(ui.get("lastUserText") or ""),
    ]
    for item in ui.get("messages", []) or []:
        if isinstance(item, dict):
            texts.append(str(item.get("text") or ""))
    haystack = "\n".join(texts).lower()
    return any(marker in haystack for marker in _CONVERSATION_LIMIT_MARKERS)


class MainConversationLimitWatchdog:
    name = "main_conversation_limit"

    @classmethod
    def scan(cls, snapshot: dict) -> List[Dict[str, object]]:
        alerts = []
        for item in snapshot.get("agents", []):
            if item.get("name") != "main":
                continue
            if not conversation_limit_reached(item.get("ui")):
                continue
            alerts.append({
                "name": "main",
                "kind": "main_conversation_limit",
                "watchdog": cls.name,
                "browser_state": str(item.get("browser_state") or "").strip(),
                "error": (
                    "Main ChatGPT conversation reached its length limit and cannot "
                    "receive further orchestration ticks."
                ),
            })
        return alerts


class BrowserSuspensionWatchdog:
    name = "browser_suspension"

    @classmethod
    def scan(cls, snapshot: dict) -> List[Dict[str, object]]:
        alerts = []
        for item in snapshot.get("agents", []):
            if item.get("name") == "main":
                continue
            binding = item.get("binding") or {}
            if isinstance(binding, dict) and binding.get("suspended"):
                continue
            state = str(item.get("browser_state") or "").strip()
            if state not in {"discarded", "frozen", "unresponsive"}:
                continue
            alerts.append({
                "name": item.get("name"),
                "kind": "browser_suspended",
                "watchdog": cls.name,
                "browser_state": state,
                "error": str(item.get("error") or "").strip(),
            })
        return alerts


class InspectErrorWatchdog:
    name = "inspect_error"

    @classmethod
    def scan(cls, snapshot: dict) -> List[Dict[str, object]]:
        alerts = []
        for item in snapshot.get("agents", []):
            if item.get("name") == "main":
                continue
            binding = item.get("binding") or {}
            if isinstance(binding, dict) and binding.get("suspended"):
                continue
            state = str(item.get("browser_state") or "").strip()
            if state in {"discarded", "frozen", "unresponsive"}:
                continue
            error = str(item.get("error") or "").strip()
            if error:
                alerts.append({
                    "name": item.get("name"),
                    "kind": "inspect_error",
                    "watchdog": cls.name,
                    "error": error,
                })
        return alerts


class GeneratingStallWatchdog:
    name = "generating_stall"

    @classmethod
    def scan(cls, snapshot: dict) -> List[Dict[str, object]]:
        alerts = []
        for item in snapshot.get("agents", []):
            if item.get("name") == "main":
                continue
            binding = item.get("binding") or {}
            if isinstance(binding, dict) and binding.get("suspended"):
                continue
            ui = item.get("ui") or {}
            if isinstance(ui, dict) and ui.get("stuckGenerating"):
                alerts.append({
                    "name": item.get("name"),
                    "kind": "stuck_generating",
                    "watchdog": cls.name,
                })
        return alerts


class SendTransactionWatchdog:
    name = "send_transaction"

    @classmethod
    def scan(cls, snapshot: dict) -> List[Dict[str, object]]:
        alerts = []
        for item in snapshot.get("agents", []):
            binding = item.get("binding") or {}
            health = binding.get("send_health") if isinstance(binding, dict) else None
            if not isinstance(health, dict) or health.get("last_ok") is not False:
                continue
            ack_timeout = bool(health.get("ack_timeout"))
            alerts.append({
                "name": item.get("name"),
                "kind": "send_ack_timeout" if ack_timeout else "send_transaction_failed",
                "watchdog": cls.name,
                "error": str(health.get("last_error") or ""),
                "command_id": str(health.get("last_command_id") or ""),
                "consecutive_failures": int(health.get("consecutive_failures") or 0),
                "may_have_executed": bool(health.get("may_have_executed")),
            })
        return alerts


class RequestFailureWatchdog:
    name = "chatgpt_request_failure"

    @classmethod
    def scan(cls, snapshot: dict) -> List[Dict[str, object]]:
        alerts = []
        for item in snapshot.get("agents", []):
            ui = item.get("ui") or {}
            if not isinstance(ui, dict) or not ui.get("requestFailed"):
                continue
            alerts.append({
                "name": item.get("name"),
                "kind": "chatgpt_request_failed",
                "watchdog": cls.name,
                "browser_state": str(item.get("browser_state") or "").strip(),
                "error": "ChatGPT UI exposes a retryable request failure.",
            })
        return alerts


class BrowserLifecycleWatchdog:
    name = "browser_lifecycle"

    IMMEDIATE_KINDS = {
        "extension_connected",
        "extension_disconnected",
        "tab_created",
        "tab_removed",
        "tab_unmanaged",
    }
    LIFECYCLE_KEYS = {"discarded", "frozen", "url", "status"}

    @classmethod
    def requires_tick(cls, event: dict) -> bool:
        if not isinstance(event, dict):
            return False
        kind = str(event.get("kind") or "")
        if kind in cls.IMMEDIATE_KINDS:
            return True
        if kind != "tab_lifecycle":
            return False
        changes = event.get("changes") or {}
        if not isinstance(changes, dict):
            return False
        return any(key in changes for key in cls.LIFECYCLE_KEYS)


SNAPSHOT_WATCHDOGS = (
    MainConversationLimitWatchdog,
    BrowserSuspensionWatchdog,
    InspectErrorWatchdog,
    GeneratingStallWatchdog,
    SendTransactionWatchdog,
    RequestFailureWatchdog,
)


def collect_snapshot_alerts(snapshot: dict) -> List[Dict[str, object]]:
    alerts = []
    for watchdog in SNAPSHOT_WATCHDOGS:
        alerts.extend(watchdog.scan(snapshot))
    return alerts
