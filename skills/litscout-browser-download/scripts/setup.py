"""Idempotent per-user setup, detached startup and short-lived pairing. Stdlib only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zipfile import ZipFile

from local_config import configuration, validate_base

SKILL = Path(__file__).resolve().parents[1]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(base, path, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode() if payload is not None else None
    with build_opener(ProxyHandler({}), NoRedirect()).open(Request(base + path, data=data, headers=headers), timeout=3) as response:
        return json.load(response)


def existing_service(base, root):
    port = urlsplit(base).port
    with socket.socket() as probe:
        probe.settimeout(1)
        occupied = probe.connect_ex(("127.0.0.1", port)) == 0
    if not occupied:
        return None
    try:
        health = request(base, "/healthz")
        if health.get("service") != "litscout-download":
            raise ValueError("PORT_OCCUPIED_BY_OTHER_SERVICE")
        token = (root / "agent-token").read_text(encoding="utf-8").strip()
        browsers = request(base, "/v1/browsers", token)
        return health, browsers
    except (OSError, ValueError, KeyError) as error:
        raise ValueError("PORT_OR_DATA_CONFLICT: existing service cannot be authenticated with this data directory; no process was stopped") from error


@contextmanager
def setup_lock(root):
    # OS locks are released even if the installer crashes; no stale lock deletion.
    with (root / "setup.lock").open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("SETUP_ALREADY_RUNNING: wait for the other installer") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def safe_extract(archive, target):
    with ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > 32 * 1024 * 1024:
            raise ValueError("COMPANION_ARCHIVE_TOO_LARGE")
        for item in package.infolist():
            resolved = (target / item.filename).resolve()
            if (not resolved.is_relative_to(target.resolve()) or "\\" in item.filename
                    or ":" in item.filename or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("UNSAFE_COMPANION_ARCHIVE")
        package.extractall(target)


@contextmanager
def source_tree(explicit=None):
    candidate = Path(explicit).resolve() if explicit else SKILL.parents[1]
    if (candidate / "litscout_download/app.py").is_file() and (candidate / "apps/browser-extension/manifest.json").is_file():
        yield candidate
        return
    if explicit:
        raise ValueError("SOURCE_ROOT_INVALID")
    archive, manifest = SKILL / "assets/companion.zip", SKILL / "assets/companion.json"
    if not archive.is_file() or not manifest.is_file():
        raise ValueError("COMPANION_BUNDLE_MISSING: use the complete 0.1.1+ Skill release or --source-root")
    expected = json.loads(manifest.read_text(encoding="utf-8"))["sha256"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("COMPANION_CHECKSUM_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="litscout-setup-source-") as temporary:
        target = Path(temporary)
        safe_extract(archive, target)
        yield target


def source_files(source):
    paths = [source / name for name in ("pyproject.toml", "requirements-runtime.txt", "LICENSE")]
    paths += sorted((source / "litscout_download").glob("*.py"))
    paths += [path for path in sorted((source / "apps/browser-extension").rglob("*")) if path.is_file()
              and "tests" not in path.relative_to(source).parts and path.name != "package.json"]
    if any(path.is_symlink() for path in paths):
        raise ValueError("SOURCE_SYMLINK_NOT_ALLOWED")
    return paths


def stage_runtime(source, root):
    files = source_files(source)
    checksum = hashlib.sha256()
    for path in files:
        checksum.update(path.relative_to(source).as_posix().encode())
        checksum.update(path.read_bytes())
    version = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    target = root / "runtime" / (version + "-" + checksum.hexdigest()[:12])
    tree = target / "source"
    for path in files:
        destination = tree / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(path, destination)
        elif destination.read_bytes() != path.read_bytes():
            raise ValueError("RUNTIME_CONTENT_CHANGED: preserve the runtime and inspect its files")
    return target, tree, version


def install_skill(root, source, skills_root=None):
    parent = Path(skills_root or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "skills").expanduser().resolve()
    target = parent / "litscout-browser-download"
    if target.is_symlink():
        raise ValueError("SKILL_TARGET_SYMLINK_NOT_ALLOWED")
    if target.resolve() == SKILL.resolve():
        return target
    if target.exists():
        # Keep unknown user edits outside the active skills directory.
        backup = root / "backups" / ("skill-" + str(time.time_ns()))
        shutil.copytree(target, backup, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    target.mkdir(parents=True, exist_ok=True)
    for path in SKILL.rglob("*"):
        relative = path.relative_to(SKILL)
        if any(part in {"__pycache__", ".git", "assets"} for part in relative.parts) or not path.is_file():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    # An installed Skill remains usable after the downloaded source folder is removed.
    assets = target / "assets"
    assets.mkdir(exist_ok=True)
    from zipfile import ZIP_DEFLATED
    archive = assets / "companion.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as package:
        for path in source_files(source):
            package.write(path, path.relative_to(source).as_posix())
    (assets / "companion.json").write_text(json.dumps({"sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}), encoding="utf-8")
    return target


def stage_extension(tree, root):
    target = root / "extension"
    source = tree / "apps/browser-extension"
    if target.is_symlink():
        raise ValueError("EXTENSION_TARGET_SYMLINK_NOT_ALLOWED")
    if target.exists():
        changed = any(not (target / path.relative_to(source)).is_file() or
                      (target / path.relative_to(source)).read_bytes() != path.read_bytes()
                      for path in source.rglob("*") if path.is_file())
        if changed:
            backup = root / "backups" / ("extension-" + str(time.time_ns()))
            shutil.copytree(target, backup)
    shutil.copytree(source, target, dirs_exist_ok=True)
    return target


def prepare_python(runtime, tree, log):
    environment = runtime / "venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = runtime / "installed.json"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, stdout=log, stderr=log, timeout=90)
    if not marker.exists():
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-c", str(tree / "requirements-runtime.txt"), str(tree)],
                       check=True, stdout=log, stderr=log, timeout=360)
        marker.write_text('{"installed":true}', encoding="utf-8")
    return python


def start_service(python, root, base, log_path):
    env = {key: value for key, value in os.environ.items() if "CDK" not in key.upper()
           and not key.startswith("LITSCOUT_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
    env["LITSCOUT_DOWNLOAD_DATA_DIR"] = str(root)
    if os.environ.get("LITSCOUT_DOWNLOAD_BROWSER_DOWNLOAD_DIR"):
        env["LITSCOUT_DOWNLOAD_BROWSER_DOWNLOAD_DIR"] = os.environ["LITSCOUT_DOWNLOAD_BROWSER_DOWNLOAD_DIR"]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with log_path.open("ab") as log:
        process = subprocess.Popen([str(python), "-m", "litscout_download.cli", "--data-dir", str(root),
            "--port", str(urlsplit(base).port), "serve"], cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **options)
    for _ in range(100):
        if process.poll() is not None:
            raise ValueError("SERVICE_START_FAILED: see the local service log")
        try:
            if existing_service(base, root):
                return process.pid
        except ValueError:
            pass  # Startup has not created the token yet.
        time.sleep(0.2)
    process.terminate()  # Only the child started by this invocation.
    process.wait(timeout=10)
    raise ValueError("SERVICE_START_TIMEOUT")


def main():
    parser = argparse.ArgumentParser(description="Set up LitScout Download and return a browser pairing code")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--install-skill", action="store_true")
    parser.add_argument("--skills-root", type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.exit(2, "Python 3.11+ is required\n")
    try:
        root, base = configuration(args.data_dir)
        if args.port is not None:
            base = validate_base(f"http://127.0.0.1:{args.port}")
        root.mkdir(parents=True, exist_ok=True)
        with setup_lock(root):
            connected = existing_service(base, root)
            pid = None
            with source_tree(args.source_root) as source:
                runtime, tree, version = stage_runtime(source, root)
                extension = stage_extension(tree, root)
                skill = install_skill(root, source, args.skills_root) if args.install_skill else SKILL
                if not connected:
                    print("Preparing the isolated runtime; installation progress is in the local setup log.", file=sys.stderr, flush=True)
                    with (root / "setup.log").open("ab") as log:
                        python = prepare_python(runtime, tree, log)
                    # Recheck before launch; never take over an occupied port.
                    connected = existing_service(base, root)
                    if not connected:
                        pid = start_service(python, root, base, root / "service.log")
            config = {"baseUrl": base, "dataDir": str(root), "extensionPath": str(extension)}
            temporary = root / "client.json.tmp"
            temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
            temporary.replace(root / "client.json")
            token = (root / "agent-token").read_text(encoding="utf-8").strip()
            pair = request(base, "/v1/pair-codes", token, {})
            browsers = request(base, "/v1/browsers", token)
            result = {"status": "ready", **config, "skillPath": str(skill), "version": version,
                "serviceVersion": request(base, "/healthz")["version"], "serviceReused": connected is not None,
                "startedPid": pid, "code": pair["code"],
                "connectionCode": f"litscout-download:v1:{urlsplit(base).port}:{pair['code']}",
                "expiresInSeconds": pair["expiresInSeconds"], "pairedBrowsers": len([b for b in browsers if not b["revoked"]]),
                "browserInstallRequired": not any(not b["revoked"] for b in browsers),
                "browserInstructions": "Existing extension: paste code. New install: enable developer mode at edge://extensions and load extensionPath; then paste connectionCode (0.1.1+)."}
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        # Do not echo subprocess output, URLs containing credentials or tokens.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__ + ": inspect setup.log/service.log in the selected data directory"
        print(json.dumps({"status": "error", "message": message}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
