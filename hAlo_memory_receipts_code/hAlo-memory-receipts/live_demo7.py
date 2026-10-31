"""Live demo server: the Memory Receipts page plus a real Reactor connection, on your own computer.

    python live_demo.py --base-url https://t05.api.reactor-hackathon.rxai.dev --agent-id agt_...

Then open http://127.0.0.1:8800 in your browser. REACTOR_ACCESS_KEY must be set in this Terminal (it stays here,
never in the page). Needs final_run2.py, client.py, dev/histories.jsonl and memory_receipts_live.html in this folder.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import final_run2 as fr  # noqa: E402  our search (retrieve) and the client wrapper

LOCK = threading.Lock()  # Reactor takes one question at a time
STATE = {"history": None}


def load_histories(path: Path, wanted: set[str]) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8-sig").split("\n"):
        if line.strip() and any(w in line[:80] for w in wanted):
            h = json.loads(line)
            if h["history_id"] in wanted:
                out[h["history_id"]] = h
    return out


def ask(args, history: dict, question: str, question_date: str) -> dict:
    q = {"question": question, "question_date": question_date}
    evidence = fr.retrieve(q, history)
    with LOCK, tempfile.TemporaryDirectory() as tmp:
        ev = Path(tmp) / "evidence.json"
        ev.write_text(json.dumps(evidence), encoding="utf-8")
        result = {}
        for attempt in range(3):
            if not STATE["history"]:
                new = fr.client(args, "--new-history")
                if new.get("status") != "history":
                    return {"status": "error", "error": new.get("error") or "could not start a Reactor chat"}
                STATE["history"] = new["history_id"]
            extra = ["--history", STATE["history"], "--question", question, "--question-date", question_date,
                     "--question-id", "q_live_demo"]
            if evidence:
                extra += ["--evidence", str(ev)]
            result = fr.client(args, *extra)
            if result.get("status") == "answered":
                break
            if (result.get("code") or result.get("status")) in ("history_changed", "closed", "history_required"):
                STATE["history"] = None  # someone started a new chat on the agent: open our own and ask again
    cited = [c["id"] for c in (result.get("citations") or []) if isinstance(c, dict) and c.get("id")]
    answer = re.sub(r"\s*\[\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\]", "", str(result.get("answer") or "")).strip()
    return {"status": result.get("status"), "answer": answer, "abstained": result.get("abstained"),
            "citations": cited, "latency_s": result.get("latency_s"),
            "error": result.get("error") or result.get("text"), "sent": [e["id"] for e in evidence]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Memory Receipts with live Reactor answers")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--histories", type=Path, default=HERE / "dev" / "histories.jsonl")
    ap.add_argument("--page", type=Path, default=HERE / "memory_receipts_live6.html")
    ap.add_argument("--port", type=int, default=8800)
    args = ap.parse_args()
    args.client = HERE / "client.py"

    page = args.page.read_bytes()
    marker = b'<script type="application/json" id="data">'
    start = page.index(marker) + len(marker)
    data = json.loads(page[start:page.index(b"</script>", start)].decode("utf-8").replace("<\\/", "</"))
    wanted = {q["history_id"] for q in data["questions"]}
    print(f"loading {len(wanted)} histories from {args.histories} ...", file=sys.stderr)
    histories = load_histories(args.histories, wanted)

    class Handler(BaseHTTPRequestHandler):
        def send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.send(200, b'{"live": true}', "application/json")
            elif self.path in ("/", "/index.html"):
                self.send(200, page, "text/html; charset=utf-8")
            else:
                self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/ask":
                return self.send(404, b"not found", "text/plain")
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                history = histories[req["history_id"]]
                question = str(req["question"]).strip()[:500]
                if not question:
                    raise ValueError("empty question")
                out = ask(args, history, question, str(req["question_date"]))
            except Exception as exc:  # report it to the page instead of dropping the connection
                out = {"status": "error", "error": str(exc)}
            print(f"live: {out.get('status')} {str(out.get('answer'))[:60]}", file=sys.stderr)
            self.send(200, json.dumps(out).encode(), "application/json")

        def log_message(self, fmt, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"open http://127.0.0.1:{args.port}  (Ctrl+C to stop)", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
