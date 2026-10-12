"""Verify CLI return codes for actual list-shaped companion responses."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("command,path,rows", [
    (["browsers"], "/v1/browsers", [{"id": "fixture-browser", "name": "Fixture Edge", "seen": 1}]),
    (["browsers"], "/v1/browsers", []),
    (["events", "fixture-task"], "/v1/tasks/fixture-task/events?after=0",
     [{"seq": 1, "type": "task_created", "data": {"taskName": "Fixture retrieval"}}]),
    (["events", "fixture-task"], "/v1/tasks/fixture-task/events?after=0", []),
])
def test_list_commands_exit_successfully(command, path, rows, tmp_path):
    data_dir = tmp_path / "service"
    data_dir.mkdir()
    token = "local-test-token-" + "x" * 32
    (data_dir / "agent-token").write_text(token, encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == path
            assert self.headers["Authorization"] == "Bearer " + token
            body = json.dumps(rows).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).parents[1] / "skills/litscout-browser-download/scripts/fulltext.py"),
                                     *command], capture_output=True, text=True, encoding="utf-8", timeout=15,
                                    env={**os.environ, "LITSCOUT_DOWNLOAD_DATA_DIR": str(data_dir),
                                         "LITSCOUT_DOWNLOAD_API_BASE_URL": f"http://127.0.0.1:{server.server_port}"})
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == rows
