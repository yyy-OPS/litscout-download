"""Shared local configuration; never use the legacy fulltext instance."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def validate_directory(value):
    root = Path(value).expanduser().resolve()
    legacy = (Path.home() / ".litscout-fulltext").resolve()
    if (root == Path.home().resolve() or root == Path(root.anchor)
            or root.is_relative_to(legacy) or legacy.is_relative_to(root)
            or str(root).startswith(("\\\\", "//"))):
        raise ValueError("Use a dedicated local download directory; the legacy fulltext directory is protected")
    return root


def validate_base(value):
    parts = urlsplit(value)
    if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.username or parts.password
            or parts.path not in {"", "/"} or parts.query or parts.fragment
            or not parts.port or not 1024 <= parts.port <= 65535):
        raise ValueError("Use a loopback HTTP port between 1024 and 65535")
    return value.rstrip("/")


def configuration(data_dir=None):
    root = validate_directory(data_dir or os.environ.get("LITSCOUT_DOWNLOAD_DATA_DIR") or Path.home() / ".litscout-download")
    path = root / "client.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    base = validate_base(os.environ.get("LITSCOUT_DOWNLOAD_API_BASE_URL") or saved.get("baseUrl") or "http://127.0.0.1:57133")
    return root, base
