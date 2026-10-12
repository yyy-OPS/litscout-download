"""Start the companion; use the standalone Skill client for Agent commands."""
import argparse
import os
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="LitScout Download local companion")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LITSCOUT_DOWNLOAD_DATA_DIR") or Path.home() / ".litscout-download"))
    parser.add_argument("--port", type=int, default=57133)
    parser.add_argument("command", choices=["serve"])
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    legacy = (Path.home() / ".litscout-fulltext").resolve()
    if args.data_dir.resolve().is_relative_to(legacy):
        parser.error("Legacy fulltext data directory is protected")
    import uvicorn
    from .app import make_app
    uvicorn.run(make_app(args.data_dir, port=args.port), host="127.0.0.1", port=args.port, workers=1, access_log=False)

if __name__ == "__main__":
    main()
