"""Optional, credential-safe model PoC using OpenAI's supported Responses API.

Default invocation exercises the complete manager/worker path against a local
mock server without credentials, charges, or access to ChatGPT web pages.
--live requires OPENAI_API_KEY and makes three billable API requests.
"""
import argparse
import json
import os
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from web_agent_swarm.state import DurableStore

MODEL_DEFAULT = "gpt-6-luna"
API_URL = "https://api.openai.com/v1/responses"


class ModelResponseError(RuntimeError):
    pass


class ResponsesClient:
    def __init__(self, api_key, model=MODEL_DEFAULT, url=API_URL):
        self.api_key = api_key
        self.model = model
        self.url = url

    def respond(self, instructions, text):
        payload = json.dumps({
            "model": self.model,
            "instructions": instructions,
            "input": text,
            "max_output_tokens": 240,
            "store": False,
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }
        req = request.Request(self.url, data=payload, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=40) as response:
                result = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise ModelResponseError("API HTTP status {}".format(exc.code)) from None
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ModelResponseError(
                "API result is unknown (no retry): {}".format(type(exc).__name__)
            ) from None

        if not isinstance(result, dict) or result.get("status") != "completed":
            raise ModelResponseError("Model response did not complete")
        parts = []
        for item in result.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    parts.append(str(part.get("text") or ""))
        content = "".join(parts).strip()
        if not content:
            raise ModelResponseError("Model returned no text")
        return content


def run_workflow(client, store, input_text="Proof of concept"):
    task = store.task_create("Handle triggered input", owner="implementer",
                             payload={"text": input_text}, max_attempts=2)
    task_id = task["task_id"]
    commands = []

    def step(name, instructions, content):
        command_id = uuid.uuid4().hex
        store.send_transaction_record(
            command_id, name=name, status="attempting",
        )
        try:
            output = client.respond(instructions, content)
        except Exception as exc:
            # No blind retry: the request may have reached the API.
            store.send_transaction_record(
                command_id, name=name, status="ack_timeout",
                result={"error": type(exc).__name__},
                may_have_executed=True,
            )
            raise
        store.send_transaction_record(
            command_id, name=name, status="ok", result={"output": output},
        )
        commands.append(command_id)
        return output

    destination = step(
        "manager", "Reply with exactly WORKER-1. Do not add other text.",
        "Route this incoming event to a worker: " + input_text,
    )
    if destination.strip().upper() != "WORKER-1":
        raise ModelResponseError("Manager did not select the intended worker")

    claimed = store.task_claim(task_id, "worker-1", lease_seconds=90)
    if claimed["claimed_by"] != "worker-1":
        raise ModelResponseError("Worker lease was not established")
    completed_text = step(
        "worker-1",
        "Transform the user input to uppercase. Reply only with the result.",
        input_text,
    )
    if completed_text.strip() != input_text.upper():
        raise ModelResponseError("Worker returned an invalid transformation")
    verdict = step(
        "manager",
        "Reply with exactly ACCEPT when the result is the uppercase form of the original. Otherwise reply REJECT.",
        json.dumps({"original": input_text, "result": completed_text}),
    )
    if verdict.strip().upper() != "ACCEPT":
        raise ModelResponseError("Manager did not accept the worker output")
    finished = store.task_release(
        task_id, "worker-1", status="done",
        summary="Approved manager-worker API proof of concept",
        result={"output": completed_text},
    )
    if finished["status"] != "done":
        raise ModelResponseError("Durable task did not finish")
    return {
        "result": "PASS",
        "model": client.model,
        "manager_calls": 2,
        "worker_calls": 1,
        "durable_status": finished["status"],
        "task_attempts": finished["attempts"],
        "final_output": completed_text,
        "commands_recorded": len(commands),
        "chatgpt_website_accessed": False,
    }


def run_mock_server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/responses":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length))
            requests.append(data)
            index = len(requests)
            value = ("WORKER-1", "PROOF OF CONCEPT", "ACCEPT")[index - 1]
            body = json.dumps({
                "status": "completed",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": value}
                ]}]
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Make exactly three billable API requests")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    args = parser.parse_args()
    if args.live:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise SystemExit("OPENAI_API_KEY is required for --live; no API call was made")
        with tempfile.TemporaryDirectory(prefix="was-api-poc-") as tmp:
            result = run_workflow(ResponsesClient(key, model=args.model),
                                  DurableStore(Path(tmp)))
            result["mode"] = "live_api"
    else:
        server, thread, requests = run_mock_server()
        try:
            with tempfile.TemporaryDirectory(prefix="was-api-mock-") as tmp:
                client = ResponsesClient(
                    "synthetic-local-token", model=args.model,
                    url="http://127.0.0.1:{}/v1/responses".format(server.server_port),
                )
                result = run_workflow(client, DurableStore(Path(tmp)))
                if len(requests) != 3 or any(item.get("store") is not False for item in requests):
                    raise AssertionError("Unexpected mock request count or storage mode")
                result["mode"] = "local_mock_no_charges"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
