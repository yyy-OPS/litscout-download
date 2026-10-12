"""Exercise the installed-style CLI across a real loopback HTTP transport."""
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def test_wait_exports_before_returning_human_handoff(tmp_path):
    pdf = b"%PDF-1.7\n" + b"paper" * 250
    expected = hashlib.sha256(pdf).hexdigest()
    receipts, checks = [], []
    data = tmp_path / "service"
    data.mkdir()
    (data / "agent-token").write_text("a" * 40)
    output = tmp_path / "workspace"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, content_type="application/json"):
            raw = json.dumps(body).encode() if content_type == "application/json" else body
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            assert self.headers["Authorization"] == "Bearer " + "a" * 40
            if self.path.endswith("/artifact"):
                self.respond(pdf, "application/pdf")
            elif "/text?" in self.path:
                self.respond({"verification": "verified", "sha256": expected, "bytes": len(pdf),
                              "pageCount": 1, "textStatus": "text_available"})
            else:
                checks.append(self.path)
                self.respond({"taskId": "task-1", "cloudJobId": "job-1", "status": "waiting_user" if len(checks) > 1 else "running",
                              "progress": {"exported": len(receipts)}, "items": [
                                  {"id": "item-1", "title": "Verified paper", "state": "completed"},
                                  {"id": "item-2", "title": "Human handoff", "state": "waiting_user"}]})

        def do_POST(self):
            assert self.path.endswith("/export")
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert Path(payload["outputPath"]).read_bytes() == pdf
            receipts.append(payload)
            self.respond({"ok": True})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).parents[1] / "skills/litscout-browser-download/scripts/fulltext.py"),
                                     "wait", "task-1", "--output-dir", str(output)], capture_output=True, text=True,
                                    encoding="utf-8", timeout=15, env={**os.environ, "LITSCOUT_DOWNLOAD_DATA_DIR": str(data),
                                    "LITSCOUT_DOWNLOAD_API_BASE_URL": f"http://127.0.0.1:{server.server_port}"})
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["status"] == "waiting_user"
    assert state["progress"]["exported"] == state["workspaceExport"]["exported"] == 1
    assert state["workspaceExport"]["pending"][0]["itemId"] == "item-2"
    assert len(receipts) == 1 and len(list(output.glob("*.pdf"))) == 1
