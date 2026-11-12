"""Drive consecutive user turns in one native Codex app-server process."""

import json
import queue
import subprocess
import threading
from types import SimpleNamespace


def run_app_server(command, env, workspace, directory, prompts, parent_model):
    events = queue.Queue()
    reviews = []
    with (directory / "app-server-stderr.txt").open("w") as stderr:
        process = subprocess.Popen(
            command,
            env=env,
            cwd=workspace,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            bufsize=1,
        )

        def pump():
            with (directory / "app-server-events.jsonl").open("w") as transcript:
                for line in process.stdout:
                    transcript.write(line)
                    transcript.flush()
                    events.put(json.loads(line))
            events.put(None)

        threading.Thread(target=pump, daemon=True).start()

        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()

        def wait(predicate):
            while True:
                value = events.get(timeout=240)
                if value is None:
                    raise RuntimeError("app-server closed stdout")
                if "id" in value and "method" in value:
                    raise RuntimeError("Unexpected server request: " + value["method"])
                if "error" in value and "id" in value:
                    raise RuntimeError(json.dumps(value["error"]))
                if value.get("method") == "item/autoApprovalReview/completed":
                    reviews.append(value["params"]["review"])
                if predicate(value):
                    return value

        try:
            send(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "guardian-minimal-repro",
                            "version": "1",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                }
            )
            wait(lambda value: value.get("id") == 1)
            send({"method": "initialized", "params": {}})
            send(
                {
                    "id": 2,
                    "method": "thread/start",
                    "params": {
                        "cwd": str(workspace),
                        "model": parent_model,
                        "approvalPolicy": "on-request",
                        "approvalsReviewer": "auto_review",
                    },
                }
            )
            thread = wait(lambda value: value.get("id") == 2)["result"]["thread"]["id"]
            for index, prompt in enumerate(prompts):
                send(
                    {
                        "id": index + 3,
                        "method": "turn/start",
                        "params": {
                            "threadId": thread,
                            "input": [
                                {"type": "text", "text": prompt, "textElements": []}
                            ],
                        },
                    }
                )
                wait(lambda value, request_id=index + 3: value.get("id") == request_id)
                completed = wait(
                    lambda value: (
                        value.get("method") == "turn/completed"
                        and value.get("params", {}).get("threadId") == thread
                    )
                )
                turn = completed["params"]["turn"]
                if turn.get("status") != "completed":
                    raise RuntimeError("Turn did not complete: " + json.dumps(turn))
        finally:
            process.stdin.close()
            process.wait(timeout=30)
    return SimpleNamespace(returncode=process.returncode, reviews=reviews)
