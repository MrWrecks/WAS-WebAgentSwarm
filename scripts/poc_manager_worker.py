"""End-to-end localhost PoC: externally triggered manager, worker, crash, resume.

Uses the actual Node bridge, Python SwarmRuntime and DurableStore.
The two browser sessions are synthetic: no ChatGPT website or output is accessed.
No persistent services or browser profiles are modified.
"""
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_agent_swarm.bridge import BridgeClient
from web_agent_swarm.state import DurableStore
from web_agent_swarm.swarm import SwarmRuntime

MANAGER_URL = "https://chatgpt.com/c/11111111-1111-4111-8111-111111111111"
WORKER_URL = "https://chatgpt.com/c/22222222-2222-4222-8222-222222222222"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def await_true(predicate, label, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.08)
    raise AssertionError("Timed out: " + label)


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def simulate_external_webhook(event_queue):
    class TriggerHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/trigger":
                self.send_error(404)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048:
                self.send_error(400)
                return
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            event_queue.put(data)
            self.send_response(202)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), TriggerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:{}/trigger".format(server.server_port)
        event = {"source": "synthetic-external-webhook", "payload": "Proof of concept"}
        # Separate process imitates an outside webhook sender; no internet access.
        source_code = (
            "import json,sys,urllib.request;"
            "data=json.dumps(json.loads(sys.argv[2])).encode();"
            "req=urllib.request.Request(sys.argv[1],data=data,"
            "headers={'Content-Type':'application/json'},method='POST');"
            "r=urllib.request.urlopen(req,timeout=3);"
            "assert r.status==202"
        )
        subprocess.run(
            [sys.executable, "-c", source_code, url, json.dumps(event)],
            check=True, timeout=5, stdout=subprocess.DEVNULL,
        )
        return event_queue.get(timeout=4)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def run():
    bridge_process = None
    synthetic_process = None
    with tempfile.TemporaryDirectory(prefix="was-poc-") as temp:
        base = Path(temp)
        state_root = base / "state"
        session_path = base / "synthetic-sessions.json"
        token = secrets.token_hex(32)
        port = free_port()
        base_url = "http://127.0.0.1:{}".format(port)
        env = os.environ.copy()
        env.update({
            "WEB_SWARM_BIND": "127.0.0.1",
            "WEB_SWARM_PORT": str(port),
            "WEB_SWARM_BRIDGE_TOKEN": token,
            "PYTHONPATH": str(ROOT / "src"),
        })
        bridge = BridgeClient(base_url, token=token)
        store = DurableStore(state_root)

        def launch_synthetic_sessions():
            return subprocess.Popen(
                ["node", str(ROOT / "bridge" / "poc-synthetic-extension.mjs"),
                 str(port), token, str(state_root), str(session_path), sys.executable],
                cwd=str(ROOT / "bridge"), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        try:
            bridge_process = subprocess.Popen(
                ["node", str(ROOT / "bridge" / "server.js")],
                cwd=str(ROOT / "bridge"), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            await_true(lambda: bridge.health().get("ok"), "bridge startup")
            synthetic_process = launch_synthetic_sessions()
            await_true(
                lambda: bridge.health().get("extensionClients") == 1,
                "two synthetic sessions connected",
            )

            store.bind("main", {
                "kind": "main", "conversation_id": MANAGER_URL.rsplit("/", 1)[-1],
                "url_at_bind": MANAGER_URL,
            })
            store.bind("worker-1", {
                "kind": "worker", "role": "implementer",
                "conversation_id": WORKER_URL.rsplit("/", 1)[-1],
                "url_at_bind": WORKER_URL,
            })
            runtime = SwarmRuntime(bridge=bridge, store=store)
            webhook = simulate_external_webhook(queue.Queue())
            assert webhook["source"] == "synthetic-external-webhook"
            task = store.task_create(
                "Process external trigger", owner="implementer",
                payload={"text": webhook["payload"]},
                max_attempts=2,
            )
            task_id = task["task_id"]
            manager_sent = runtime.send("main", json.dumps({
                "type": "trigger", "source": webhook["source"], "task_id": task_id
            }))
            assert manager_sent["ok"], ("manager dispatch failed", manager_sent)

            # The first worker takes the durable lease and then crashes before ACK.
            failed_ack = runtime.send("worker-1", json.dumps({
                "action": "claim_then_crash", "task_id": task_id
            }))
            assert not failed_ack["ok"] and failed_ack["send_health"]["may_have_executed"], failed_ack
            assert failed_ack["send_transaction"]["status"] == "ack_timeout", failed_ack
            await_true(lambda: synthetic_process.poll() is not None, "synthetic worker crash")
            leased = store.load()["tasks"][task_id]
            assert leased["status"] == "active"
            assert leased["claimed_by"] == "worker-1"
            assert leased["attempts"] == 1

            # Restore transport and resume the original lease, not a second claim.
            synthetic_process = launch_synthetic_sessions()
            await_true(
                lambda: bridge.health().get("extensionClients") == 1,
                "synthetic worker restart",
            )
            restarted_store = DurableStore(state_root)
            recovered = SwarmRuntime(bridge=bridge, store=restarted_store)
            assert restarted_store.load()["tasks"][task_id]["claimed_by"] == "worker-1"
            completed_ack = recovered.send("worker-1", json.dumps({
                "action": "resume", "task_id": task_id
            }))
            assert completed_ack["ok"], ("worker resume failed", completed_ack)
            completed = restarted_store.load()["tasks"][task_id]
            assert completed["status"] == "done"
            assert completed["attempts"] == 1
            assert completed["result"]["output"] == "PROOF OF CONCEPT"

            manager_done = recovered.send("main", json.dumps({
                "type": "completion", "task_id": task_id,
                "result": completed["result"]
            }))
            assert manager_done["ok"], ("manager completion failed", manager_done)
            assert len(restarted_store.send_transactions(limit=10)) == 4

            return {
                "result": "PASS",
                "trigger": "separate-process localhost webhook",
                "sessions": "1 synthetic manager + 1 synthetic worker",
                "bridge": "real authenticated localhost WebSocket bridge",
                "coordination": "real SwarmRuntime + DurableStore",
                "worker_crashed_before_ack": True,
                "unknown_ack_preserved": True,
                "worker_resumed_original_lease": True,
                "task_attempts": completed["attempts"],
                "final_task_status": completed["status"],
                "final_output": completed["result"]["output"],
                "chatgpt_website_accessed": False,
                "persistent_services_installed": False,
            }
        finally:
            stop_process(synthetic_process)
            stop_process(bridge_process)


if __name__ == "__main__":
    try:
        print(json.dumps(run(), indent=2))
    except Exception as exc:
        print(json.dumps({"result": "FAIL", "error": str(exc)}, indent=2))
        raise
