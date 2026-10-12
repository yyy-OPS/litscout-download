from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("fulltext_export", Path(__file__).parents[1] / "skills/litscout-browser-download/scripts/fulltext_export.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

PDF = b"%PDF-1.7\n" + b"test-document" * 100
META = {"verification": "verified", "parserVerification": "verified", "sha256": hashlib.sha256(PDF).hexdigest(),
        "bytes": len(PDF), "pageCount": 2, "textStatus": "text_available"}


def task(state="completed"):
    return {"taskId": "task-1", "cloudJobId": "job-1", "items": [
        {"id": "item-1", "paper": "paper-1", "title": "CON: ../../文献?", "year": 2026,
         "identifiers": {"doi": "10.1234/example"}, "state": state}]}


def test_export_and_idempotent_reuse_with_manifest(tmp_path):
    receipts, downloads = [], []

    def request(path, payload=None):
        if payload:
            receipts.append(payload)
            assert Path(payload["outputPath"]).read_bytes() == PDF
            return {"ok": True}
        return META.copy()

    def open_artifact(path):
        downloads.append(path)
        return io.BytesIO(PDF)

    first = exporter.export_task(task(), tmp_path, request, open_artifact)
    second = exporter.export_task(task(), tmp_path, request, open_artifact)
    assert first["exported"] == second["exported"] == 1
    assert first["errors"] == second["errors"] == []
    assert len(downloads) == 1
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    manifest = json.loads(Path(first["manifestPath"]).read_text(encoding="utf-8"))
    assert manifest["items"][0]["reused"] is True
    assert manifest["items"][0]["doi"] == "10.1234/example"
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("state", ["waiting_user", "verification_required", "cancelled"])
def test_unfinished_files_are_not_read_or_exported(tmp_path, state):
    def forbidden(*args):
        pytest.fail("An unverified artifact must not be accessed")
    result = exporter.export_task(task(state), tmp_path, forbidden, forbidden)
    assert result["exported"] == 0 and result["pending"][0]["state"] == state


def test_identity_rechecked_at_export_time(tmp_path):
    result = exporter.export_task(task(), tmp_path, lambda _: {**META, "verification": "mismatch"},
                                  lambda _: pytest.fail("Must not download mismatched artifact"))
    assert result["errors"] == [{"itemId": "item-1", "code": "ARTIFACT_NOT_VERIFIED"}]


@pytest.mark.parametrize("data,code", [(b"%PDF-1.7\n" + b"x" * (len(PDF) - 9), "EXPORT_HASH_MISMATCH"),
                                      (PDF[:-10], "EXPORT_SIZE_MISMATCH"), (PDF + b"extra", "EXPORT_SIZE_MISMATCH"),
                                      (b"ERROR" + PDF[5:], "NOT_A_PDF")])
def test_integrity_failure_never_publishes(tmp_path, data, code):
    result = exporter.export_task(task(), tmp_path, lambda _: META, lambda _: io.BytesIO(data))
    assert result["errors"][0]["code"] == code
    assert not list(tmp_path.glob("*.pdf")) and not list(tmp_path.glob("*.part"))


def test_conflicting_user_file_is_preserved(tmp_path):
    target = tmp_path / exporter.filename(task()["items"][0])
    target.write_bytes(b"my-user-file")
    result = exporter.export_task(task(), tmp_path, lambda _: META, lambda _: pytest.fail("Conflict must be detected first"))
    assert result["errors"][0]["code"] == "EXPORT_FILE_CONFLICT"
    assert target.read_bytes() == b"my-user-file"


def test_foreign_manifest_is_preserved(tmp_path):
    manifest = tmp_path / "litscout-task-1-manifest.json"
    manifest.write_text('{"user": "data"}', encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="EXPORT_MANIFEST_CONFLICT"):
        exporter.export_task(task(), tmp_path, None, None)
    assert json.loads(manifest.read_text()) == {"user": "data"}


def test_local_absolute_directory_is_required():
    for directory in ["relative", "//host/share"]:
        with pytest.raises(exporter.ExportError, match="OUTPUT_DIR_MUST_BE_LOCAL_ABSOLUTE"):
            exporter.export_task(task(), directory, None, None)


def test_windows_safe_filename_and_distinct_items():
    first = exporter.filename(task()["items"][0])
    assert first.startswith("paper-") and len(first) < 200
    assert not any(char in first for char in '<>:"/\\|?*')
    assert exporter.filename({**task()["items"][0], "id": "item-2"}) != first


def test_partial_export_and_request_failure_redacts_details(tmp_path):
    data = task()
    data["items"].append({**data["items"][0], "id": "item-2"})

    def request(path, payload=None):
        if "item-2" in path:
            raise OSError("private credential and publisher URL")
        return META if payload is None else {"ok": True}

    result = exporter.export_task(data, tmp_path, request, lambda _: io.BytesIO(PDF))
    assert result["exported"] == 1
    assert result["errors"] == [{"itemId": "item-2", "code": "EXPORT_LOCAL_REQUEST_FAILED"}]
    assert "private credential" not in Path(result["manifestPath"]).read_text()
