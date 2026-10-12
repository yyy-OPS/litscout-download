"""Install from a standalone Skill ZIP in an isolated directory; no existing profiles."""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_zip", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="litscout-install-smoke-") as folder:
        root = Path(folder)
        with ZipFile(args.skill_zip) as package:
            package.extractall(root / "unpacked")
        setup = root / "unpacked/litscout-browser-download/scripts/setup.py"
        data, skills = root / "data", root / "skills"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {key: value for key, value in os.environ.items() if "CDK" not in key.upper() and not key.startswith("LITSCOUT_")}
        command = [sys.executable, "-X", "utf8", str(setup), "--data-dir", str(data), "--port", str(port),
                   "--install-skill", "--skills-root", str(skills)]
        started = None
        try:
            print("Testing standalone Skill: creating a fresh venv and installing dependencies", flush=True)
            first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env, timeout=450)
            assert first.returncode == 0, first.stderr
            first = json.loads(first.stdout)
            started = first["startedPid"]
            assert started and first["status"] == "ready" and not first["serviceReused"]
            assert (Path(first["extensionPath"]) / "manifest.json").is_file()
            assert (skills / "litscout-browser-download/assets/companion.zip").is_file()
            repeat = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
            assert repeat.returncode == 0, repeat.stderr
            repeat = json.loads(repeat.stdout)
            assert repeat["serviceReused"] and repeat["startedPid"] is None and repeat["code"] != first["code"]
            opener = build_opener(ProxyHandler({}))
            with opener.open(Request(first["baseUrl"] + "/v1/pair", data=json.dumps({"code": repeat["code"], "name": "Isolated setup fixture"}).encode(),
                headers={"Content-Type": "application/json", "Origin": "chrome-extension://" + "e" * 32}), timeout=5) as response:
                paired = json.load(response)
            client = skills / "litscout-browser-download/scripts/fulltext.py"
            queried = subprocess.run([sys.executable, "-X", "utf8", str(client), "browsers"],
                env={**env, "LITSCOUT_DOWNLOAD_DATA_DIR": str(data)}, capture_output=True, text=True, encoding="utf-8", timeout=15)
            assert queried.returncode == 0, queried.stderr
            assert json.loads(queried.stdout)[0]["id"] == paired["browserId"]
            assert "browserToken" not in queried.stdout
            blocked = subprocess.run([*command, "--port", "80"], capture_output=True, text=True, env=env, timeout=10)
            assert blocked.returncode != 0
            print(json.dumps({"status": "passed", "checks": ["standalone_skill_bundle", "fresh_isolated_venv", "detached_startup",
                "service_authenticated_reuse", "pairing_code_accepted", "client_saved_port", "extension_staged",
                "installed_skill_self_contained", "invalid_port_refused"], "cloudCdkRequired": False}))
        finally:
            if started:
                os.kill(started, signal.SIGTERM)
                import time
                time.sleep(1)


if __name__ == "__main__":
    main()
