"""Smoke the extracted source release without using a user's installed companion."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_zip", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="litscout-release-smoke-") as folder:
        root = Path(folder)
        with ZipFile(args.source_zip) as package:
            for name in package.namelist():
                target = (root / name).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("Unsafe ZIP member")
            package.extractall(root)
        source = next(path for path in root.iterdir() if path.is_dir() and (path / "pyproject.toml").exists())
        data = root / "service-data"
        environment = {key: value for key, value in os.environ.items()
                       if "CDK" not in key.upper() and not key.startswith("LITSCOUT_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
        environment.update(LITSCOUT_DOWNLOAD_DATA_DIR=str(data),
                           LITSCOUT_DOWNLOAD_API_BASE_URL="http://127.0.0.1:57149")
        opener = build_opener(ProxyHandler({}))

        def request(path, payload=None, headers=None):
            content = json.dumps(payload).encode() if payload is not None else None
            with opener.open(Request("http://127.0.0.1:57149" + path, data=content,
                    headers={"Content-Type": "application/json", **(headers or {})}), timeout=3) as response:
                return json.load(response)

        with (root / "server.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "litscout_download.cli", "--port", "57149", "serve"],
                                       cwd=source, env=environment, stdout=log, stderr=log)
            try:
                for _ in range(60):
                    if process.poll() is not None:
                        raise RuntimeError("Extracted source server failed to start")
                    try:
                        health = request("/healthz")
                        if health["service"] == "litscout-download":
                            break
                    except OSError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("Extracted source health timed out")
                assert len((data / "agent-token").read_text()) >= 32

                def client(*arguments):
                    run = subprocess.run([sys.executable, "-X", "utf8", "skills/litscout-browser-download/scripts/fulltext.py", *arguments],
                        cwd=source, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
                    assert run.returncode == 0, run.stderr
                    return json.loads(run.stdout)

                code = client("pair-code")["code"]
                peer = request("/v1/pair", {"code": code, "name": "Release fixture browser"},
                    {"Origin": "chrome-extension://" + "c" * 32})
                assert client("browsers")[0]["id"] == peer["browserId"]
                manifest = root / "papers.json"
                manifest.write_text(json.dumps({"listId": "release-smoke", "papers": [{"title": "Standalone release smoke paper",
                    "identifiers": {"doi": "10.1234/release"}}]}), encoding="utf-8")
                task = client("submit", "--list", str(manifest), "--browser", peer["browserId"], "--key", "release-smoke-idem")
                assert client("list", task["listId"])["papers"][0]["title"] == "Standalone release smoke paper"
                assert client("cancel", task["taskId"])["status"] == "cancelled"
                print(json.dumps({"status": "passed", "checks": ["zip_extraction", "source_server_start",
                    "automatic_local_token", "bundled_skill_pair", "list_submit_read", "task_cancel"], "cdkConfigured": False}))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
