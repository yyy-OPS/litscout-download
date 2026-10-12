"""Verified, non-overwriting workspace export using only the standard library."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

MAX_BYTES = 32 * 1024 * 1024


class ExportError(ValueError):
    pass


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filename(item):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(item.get("title") or "paper"))
    title = title.strip(" .")[:90].rstrip(" .") or "paper"
    # A fixed prefix also avoids Windows reserved device names.
    year = re.sub(r"[^0-9]", "", str(item.get("year") or ""))[:4]
    identifier = re.sub(r"[^A-Za-z0-9_-]", "", str(item["id"]))
    if not identifier:
        raise ExportError("INVALID_ITEM_ID")
    return f"paper-{year + '-' if year else ''}{title}-{identifier}.pdf"


def save_pdf(target, metadata, open_artifact):
    expected, size = metadata["sha256"], metadata["bytes"]
    if not re.fullmatch(r"[a-f0-9]{64}", expected) or not isinstance(size, int) or not 5 <= size <= MAX_BYTES:
        raise ExportError("INVALID_ARTIFACT_METADATA")
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or target.stat().st_size != size or checksum(target) != expected:
            raise ExportError("EXPORT_FILE_CONFLICT")
        return True
    descriptor, temporary = tempfile.mkstemp(prefix=".litscout-", suffix=".part", dir=target.parent)
    stage = Path(temporary)
    try:
        digest, total, signature = hashlib.sha256(), 0, bytearray()
        with os.fdopen(descriptor, "wb") as output, open_artifact() as source:
            for chunk in iter(lambda: source.read(65536), b""):
                total += len(chunk)
                if total > MAX_BYTES or total > size:
                    raise ExportError("EXPORT_SIZE_MISMATCH")
                signature.extend(chunk[:max(0, 5 - len(signature))])
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if signature != b"%PDF-":
            raise ExportError("NOT_A_PDF")
        if total != size:
            raise ExportError("EXPORT_SIZE_MISMATCH")
        if digest.hexdigest() != expected:
            raise ExportError("EXPORT_HASH_MISMATCH")
        try:
            if os.name == "nt":
                os.rename(stage, target)  # Windows rename refuses an existing destination.
            else:
                os.link(stage, target)  # Atomic publication without replacement.
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.stat().st_size != size or checksum(target) != expected:
                raise ExportError("EXPORT_FILE_CONFLICT") from None
            return True
        return False
    finally:
        stage.unlink(missing_ok=True)


def export_task(task, output_dir, request, open_artifact):
    directory = Path(output_dir).expanduser()
    if not directory.is_absolute() or str(directory).startswith(("\\\\", "//")):
        raise ExportError("OUTPUT_DIR_MUST_BE_LOCAL_ABSOLUTE")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    task_id = task["taskId"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", task_id):
        raise ExportError("INVALID_TASK_ID")
    manifest_path = directory / f"litscout-{task_id}-manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        if manifest_path.is_symlink():
            raise ExportError("EXPORT_MANIFEST_CONFLICT")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or existing.get("kind") != "litscout-workspace-export" or existing.get("taskId") != task_id:
            raise ExportError("EXPORT_MANIFEST_CONFLICT")
    manifest = {"kind": "litscout-workspace-export", "schemaVersion": 1, "taskId": task_id,
                "listId": task.get("listId"), "cloudJobId": task.get("cloudJobId"), "outputDir": str(directory),
                "updatedAt": datetime.now(timezone.utc).isoformat(), "items": [], "pending": [], "errors": []}
    prefix = "/v1/tasks/" + quote(task_id, safe="") + "/items/"
    for item in task["items"]:
        if item["state"] != "completed":
            manifest["pending"].append({"itemId": item["id"], "title": item.get("title"), "state": item["state"]})
            continue
        try:
            path = prefix + quote(item["id"], safe="")
            metadata = request(path + "/text?start=1&count=1")
            if metadata.get("verification") not in {"verified", "user_approved"}:
                raise ExportError("ARTIFACT_NOT_VERIFIED")
            target = directory / filename(item)
            reused = save_pdf(target, metadata, lambda: open_artifact(path + "/artifact"))
            # Only the local Agent may report exports; the companion rechecks the file.
            request(path + "/export", {"outputPath": str(target), "sha256": metadata["sha256"], "bytes": metadata["bytes"]})
            manifest["items"].append({"itemId": item["id"], "paperId": item.get("paper"),
                "title": item.get("title"), "doi": (item.get("identifiers") or {}).get("doi"),
                "exportPath": str(target), "sha256": metadata["sha256"], "bytes": metadata["bytes"],
                "pageCount": metadata.get("pageCount"), "textStatus": metadata.get("textStatus"),
                "verification": metadata["verification"], "parserVerification": metadata.get("parserVerification"), "reused": reused})
        except (OSError, ValueError, KeyError, TypeError) as error:
            code = str(error) if isinstance(error, ExportError) else "EXPORT_LOCAL_REQUEST_FAILED"
            manifest["errors"].append({"itemId": item["id"], "code": code})
    descriptor, temporary = tempfile.mkstemp(prefix=".litscout-manifest-", suffix=".part", dir=directory)
    stage = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, manifest_path)
    finally:
        stage.unlink(missing_ok=True)
    return {"outputDir": str(directory), "manifestPath": str(manifest_path),
            "exported": len(manifest["items"]), "pending": manifest["pending"], "errors": manifest["errors"]}
