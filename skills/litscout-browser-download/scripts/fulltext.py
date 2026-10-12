"""Dependency-free client for the loopback LitScout full-text Agent API."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fulltext_export import ExportError, export_task
from local_config import configuration


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def main():
    parser = argparse.ArgumentParser(description="LitScout Download Agent API")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pair-code")
    commands.add_parser("browsers")
    lists = commands.add_parser("list")
    lists.add_argument("list_id")
    submit = commands.add_parser("submit")
    submit.add_argument("--list", required=True, type=Path, help="Paper-list JSON file")
    submit.add_argument("--browser", required=True)
    submit.add_argument("--key", required=True)
    submit.add_argument("--name", default="", help="Human-readable full-text task name")
    for name in ("status", "wait", "export", "events", "cancel", "pause", "resume", "rename", "open"):
        command = commands.add_parser(name)
        command.add_argument("task")
        if name in {"wait", "export"}:
            command.add_argument("--output-dir", required=name == "export", help="Absolute local workspace directory for verified PDFs")
        if name == "rename":
            command.add_argument("name")
        if name == "open":
            command.add_argument("item")
        if name == "events":
            command.add_argument("--after", type=int, default=0)
    text = commands.add_parser("text")
    text.add_argument("task")
    text.add_argument("item")
    text.add_argument("--start", type=int, default=1)
    text.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    try:
        data_dir, base = configuration()
    except (ValueError, OSError) as error:
        parser.error(str(error))
    try:
        token = (data_dir / "agent-token").read_text(encoding="utf-8").strip()
    except OSError:
        parser.exit(2, "Local companion token not found; start the companion using the same data directory.\n")
    if len(token) < 32:
        parser.exit(2, "Local companion token is invalid.\n")
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(path, payload=None, key=None):
        headers = {"Authorization": "Bearer " + token}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        if key:
            headers["Idempotency-Key"] = key
        with opener.open(Request(base + path, data=body, headers=headers), timeout=60) as response:
            return json.load(response)

    def open_artifact(path):
        return opener.open(Request(base + path, headers={"Authorization": "Bearer " + token}), timeout=60)

    try:
        if args.command == "pair-code":
            data = request("/v1/pair-codes", {})
        elif args.command == "browsers":
            data = request("/v1/browsers")
        elif args.command == "list":
            data = request("/v1/lists/" + quote(args.list_id, safe=""))
        elif args.command == "submit":
            payload = json.loads(args.list.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict) or "papers" not in payload:
                parser.error("List must be a JSON object containing papers")
            payload["browserId"] = args.browser
            if args.name:
                payload["taskName"] = args.name
            data = request("/v1/tasks", payload, args.key)
        else:
            path = "/v1/tasks/" + quote(args.task, safe="")
            if args.command == "events":
                data = request(path + "/events?" + urlencode({"after": args.after}))
            elif args.command == "text":
                data = request(path + "/items/" + quote(args.item, safe="") + "/text?" + urlencode({"start": args.start, "count": args.count}))
            elif args.command == "cancel":
                data = request(path + "/cancel", {})
            elif args.command in {"pause", "resume", "rename"}:
                data = request(path + "/action", {"action": args.command, "name": getattr(args, "name", "")})
            elif args.command == "open":
                data = request(path + "/items/" + quote(args.item, safe="") + "/open", {})
            elif args.command == "wait":
                until = time.monotonic() + 300
                exported_ids = None
                export_result = None
                while True:
                    data = request(path)
                    ready = tuple(item["id"] for item in data["items"] if item["state"] == "completed")
                    if args.output_dir and ready != exported_ids:
                        export_result = export_task(data, args.output_dir, request, open_artifact)
                        exported_ids = ready
                    if export_result is not None:
                        data["workspaceExport"] = export_result
                    if data["status"] != "running" or time.monotonic() >= until or export_result and export_result["errors"]:
                        if export_result is not None:
                            data = request(path)
                            data["workspaceExport"] = export_result
                        break
                    time.sleep(2)
            elif args.command == "export":
                data = export_task(request(path), args.output_dir, request, open_artifact)
            else:
                data = request(path)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        if isinstance(data, dict) and (data.get("errors") or data.get("workspaceExport", {}).get("errors")):
            parser.exit(1)
    except ExportError as error:
        parser.exit(1, str(error) + "\n")
    except HTTPError as error:
        try:
            message = json.loads(error.read(4096)).get("code", "HTTP_ERROR")
        except (ValueError, AttributeError):
            message = "HTTP_ERROR"
        parser.exit(1, f"{message} (HTTP {error.code})\n")
    except (URLError, TimeoutError, OSError, ValueError, KeyError):
        parser.exit(1, "Local companion request failed; check the process and configuration.\n")


if __name__ == "__main__":
    main()
