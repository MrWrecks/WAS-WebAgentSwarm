"""Small durable coordination store. No chat transcript is persisted by default."""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional


def default_state_dir() -> Path:
    configured = os.environ.get("WEB_SWARM_STATE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".web-agent-swarm").resolve()


class DurableStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (root or default_state_dir()).resolve()
        self.path = self.root / "state.json"

    def _empty(self) -> Dict[str, object]:
        return {
            "schema_version": 1,
            "bindings": {},
            "tasks": {},
            "shared_status": {},
        }

    def load(self) -> Dict[str, object]:
        if not self.path.is_file():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        base = self._empty()
        for key in base:
            if isinstance(data.get(key), type(base[key])):
                base[key] = data[key]
        return base

    def save(self, data: Dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp), str(self.path))

    def bind(self, name: str, binding: Dict[str, object]) -> Dict[str, object]:
        data = self.load()
        data["bindings"][str(name)] = dict(binding)
        self.save(data)
        return data["bindings"][str(name)]

    def unbind(self, name: str) -> bool:
        data = self.load()
        removed = data["bindings"].pop(str(name), None) is not None
        self.save(data)
        return removed

    def bindings(self) -> Dict[str, object]:
        return dict(self.load()["bindings"])

    def task_create(
        self,
        title: str,
        description: str = "",
        owner: str = "",
        task_id: str = "",
    ) -> Dict[str, object]:
        clean_title = str(title or "").strip()
        if not clean_title:
            raise ValueError("title is required")
        data = self.load()
        active_id = str(task_id or "").strip() or "task_" + uuid.uuid4().hex[:10]
        if active_id in data["tasks"]:
            raise ValueError("task_id already exists")
        task = {
            "task_id": active_id,
            "title": clean_title,
            "description": str(description or "").strip(),
            "owner": str(owner or "").strip(),
            "status": "ready",
            "summary": "",
            "updated_at": time.time(),
        }
        data["tasks"][active_id] = task
        self.save(data)
        return dict(task)

    def task_update(
        self,
        task_id: str,
        status: str = "",
        owner: str = "",
        summary: str = "",
    ) -> Dict[str, object]:
        data = self.load()
        task = data["tasks"].get(str(task_id))
        if not isinstance(task, dict):
            raise KeyError("unknown task_id")
        if status:
            clean_status = str(status).strip().lower()
            if clean_status not in {"ready", "active", "blocked", "review", "done", "failed"}:
                raise ValueError("invalid task status")
            task["status"] = clean_status
        if owner:
            task["owner"] = str(owner).strip()
        if summary:
            task["summary"] = str(summary).strip()
        task["updated_at"] = time.time()
        self.save(data)
        return dict(task)

    def tasks(self) -> List[Dict[str, object]]:
        values = list(self.load()["tasks"].values())
        return sorted(
            [dict(item) for item in values if isinstance(item, dict)],
            key=lambda item: (str(item.get("status")), str(item.get("task_id"))),
        )

    def status_set(self, key: str, value: object) -> Dict[str, object]:
        clean = str(key or "").strip()
        if not clean:
            raise ValueError("status key is required")
        data = self.load()
        data["shared_status"][clean] = {
            "value": value,
            "updated_at": time.time(),
        }
        self.save(data)
        return dict(data["shared_status"][clean])

    def status(self) -> Dict[str, object]:
        return dict(self.load()["shared_status"])
