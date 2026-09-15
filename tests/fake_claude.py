"""Faux `claude` pour les tests : imite `auth status`, `plugin list --json` et le protocole
stream-json de `claude -p` (texte en flux, résultat, interruption). Aucun appel réseau."""
import json
import os
import sys
import threading
import time

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
MODE = os.environ.get("FAKE_CLAUDE_MODE", "ok")
args = sys.argv[1:]
lock = threading.Lock()


def emit(payload):
    with lock:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if args[:2] == ["auth", "status"]:
    logged_in = MODE != "logged_out"
    print(json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai" if logged_in else "none"}))
    sys.exit(0 if logged_in else 1)
if args[:2] == ["plugin", "list"]:
    print(json.dumps([{"id": "sage@sage", "enabled": True}, {"id": "vieux@x", "enabled": False}]))
    sys.exit(0)

settings = {}
if "--settings" in args:
    with open(args[args.index("--settings") + 1], encoding="utf-8") as file:
        settings = json.load(file)
if log := os.environ.get("FAKE_CLAUDE_LOG"):
    env = {k: v for k, v in os.environ.items() if k.startswith(("CLAUDE", "ANTHROPIC", "ENABLE_CLAUDEAI"))}
    with open(log, "a", encoding="utf-8") as file:
        file.write(json.dumps({"argv": args, "settings": settings, "env": env}) + "\n")

interrupted = threading.Event()


def answer(content):
    emit({"type": "system", "subtype": "init", "model": "fake"})
    if MODE == "error":
        emit({"type": "result", "subtype": "success", "is_error": True,
              "result": "Failed to authenticate: OAuth session expired and could not be refreshed"})
        return
    long_answer = "longue" in content
    words = ["mot"] * 200 if long_answer else f"Reçu : {content}".split(" ")
    for index, word in enumerate(words):
        if interrupted.is_set():
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
            return
        text = word if index == 0 else " " + word
        emit({"type": "stream_event",
              "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})
        if long_answer:
            time.sleep(0.05)
    emit({"type": "result", "subtype": "success", "is_error": False, "duration_api_ms": 12,
          "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": len(words)}})


worker = None
for line in sys.stdin:
    message = json.loads(line)
    if message["type"] == "control_request":
        if message["request"]["subtype"] == "interrupt":
            interrupted.set()
        emit({"type": "control_response",
              "response": {"subtype": "success", "request_id": message["request_id"], "response": {}}})
    elif message["type"] == "user":
        if worker:
            worker.join()
        interrupted.clear()
        worker = threading.Thread(target=answer, args=(message["message"]["content"],))
        worker.start()
if worker:
    worker.join()
