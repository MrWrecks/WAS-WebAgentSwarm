"""HTTP client for the local browser companion bridge."""

import json
import os
from typing import Dict, Optional
from urllib import error, parse, request


class BridgeClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 4.0) -> None:
        self.base_url = (
            base_url
            or os.environ.get("WEB_SWARM_BRIDGE_URL")
            or "http://127.0.0.1:8788"
        ).rstrip("/")
        self.timeout = max(0.2, float(timeout))

    def _json(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, object]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, object]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=timeout or self.timeout) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
                return body if isinstance(body, dict) else {
                    "ok": False,
                    "error": "bridge returned non-object JSON",
                }
        except error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8") or "{}")
                if isinstance(body, dict):
                    body.setdefault("ok", False)
                    body.setdefault("http_status", exc.code)
                    return body
            except Exception:
                pass
            return {"ok": False, "http_status": exc.code, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": "bridge unavailable: {0}".format(exc)}

    def health(self) -> Dict[str, object]:
        result = self._json("/health", timeout=1.5)
        result["base_url"] = self.base_url
        return result

    def tabs(self) -> Dict[str, object]:
        return self._json("/tabs", timeout=6.0)

    def open_tab(
        self,
        url: str = "https://chatgpt.com/",
        active: bool = False,
    ) -> Dict[str, object]:
        return self._json(
            "/open",
            method="POST",
            payload={"url": url, "active": bool(active)},
            timeout=6.0,
        )

    def inspect(self, tab_id: int, tail: int = 6) -> Dict[str, object]:
        query = parse.urlencode({
            "tabId": int(tab_id),
            "tail": max(1, min(int(tail), 20)),
        })
        return self._json("/inspect?" + query, timeout=7.0)

    def send(self, tab_id: int, text: str) -> Dict[str, object]:
        message = str(text or "").strip()
        if not message:
            return {"ok": False, "error": "text is required"}
        return self._json(
            "/send",
            method="POST",
            payload={"tabId": int(tab_id), "text": message},
            timeout=9.0,
        )
