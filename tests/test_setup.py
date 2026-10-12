import importlib.util
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills/litscout-browser-download/scripts"
sys.path.insert(0, str(SCRIPTS))
from local_config import configuration, validate_base, validate_directory
spec = importlib.util.spec_from_file_location("download_setup", SCRIPTS / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.mark.parametrize("base", ["http://127.0.0.1:80", "http://localhost:57133", "https://example.org",
    "http://127.0.0.1:57133/path", "http://u:p@127.0.0.1:57133", "http://127.0.0.1:99999"])
def test_forbid_invalid_ports_and_remote_origins(base):
    with pytest.raises(ValueError):
        validate_base(base)


@pytest.mark.parametrize("port", [1024, 57123, 57133, 65535])
def test_custom_loopback_port_is_allowed(port):
    assert validate_base(f"http://127.0.0.1:{port}/") == f"http://127.0.0.1:{port}"


def test_legacy_directory_and_home_are_protected():
    for path in [Path.home(), Path.home() / ".litscout-fulltext", Path.home() / ".litscout-fulltext/nested"]:
        with pytest.raises(ValueError):
            validate_directory(path)


def test_custom_port_is_loaded_without_system_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("LITSCOUT_DOWNLOAD_API_BASE_URL", raising=False)
    (tmp_path / "client.json").write_text(json.dumps({"baseUrl": "http://127.0.0.1:57145"}))
    root, base = configuration(tmp_path)
    assert root == tmp_path.resolve() and base.endswith(":57145")


def test_setup_lock_is_released_and_blocks_concurrent_setup(tmp_path):
    with setup.setup_lock(tmp_path):
        with pytest.raises(ValueError, match="SETUP_ALREADY_RUNNING"):
            with setup.setup_lock(tmp_path):
                pytest.fail("Concurrent installer entered")
    with setup.setup_lock(tmp_path):
        pass


def test_archive_traversal_is_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with ZipFile(archive, "w") as package:
        package.writestr("../escape.txt", "bad")
    with pytest.raises(ValueError, match="UNSAFE_COMPANION_ARCHIVE"):
        setup.safe_extract(archive, tmp_path / "extracted")
    assert not (tmp_path / "escape.txt").exists()


def test_foreign_service_is_not_reused_or_stopped(tmp_path, monkeypatch):
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(setup, "request", lambda *args, **kwargs: {"service": "other-service"})
        with pytest.raises(ValueError, match="PORT_OR_DATA_CONFLICT"):
            setup.existing_service(f"http://127.0.0.1:{port}", tmp_path)
        assert listener.fileno() >= 0


def test_wrong_token_cannot_reuse_another_instance(tmp_path, monkeypatch):
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(setup, "request", lambda *args, **kwargs: {"service": "litscout-download"})
        with pytest.raises(ValueError, match="PORT_OR_DATA_CONFLICT"):
            setup.existing_service(f"http://127.0.0.1:{port}", tmp_path)
        assert not (tmp_path / "agent-token").exists()
