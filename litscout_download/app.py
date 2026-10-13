from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .store import Problem, Store, digest
from .downloads import Downloads
from .schema import TaskRequest

MAX_BYTES = 32 * 1024 * 1024
DISK_LIMIT = 1024 * 1024 * 1024
EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}$")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairRequest(Model):
    code: str = Field(min_length=20, max_length=80)
    name: str = Field(default="Campus browser", min_length=1, max_length=80)


class BrowserReport(Model):
    state: str
    reason: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9_-]*$")
    tabId: int | None = Field(default=None, ge=0)


class UserAction(Model):
    action: str
    name: str = Field(default="", max_length=120)


class SavedDownload(Model):
    downloadId: int = Field(ge=0)
    filename: str = Field(min_length=1, max_length=1024)


class WorkspaceExport(Model):
    outputPath: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=5, le=MAX_BYTES)


class OpenResult(Model):
    tabId: int | None = Field(default=None, ge=0)


class Files:
    def __init__(self, store):
        self.store = store
        self.slots = asyncio.Semaphore(2)

    def capacity(self):
        usage = sum(file.stat().st_size for file in (self.store.root / "artifacts").rglob("*") if file.is_file())
        if usage + MAX_BYTES * 2 > DISK_LIMIT or shutil.disk_usage(self.store.root).free < MAX_BYTES * 3:
            raise Problem("LOCAL_STORAGE_FULL", 507)

    async def save(self, item, chunks, browser, lease):
        async with self.slots:
            self.capacity()
            directory = Path(tempfile.mkdtemp(prefix="capture-", dir=self.store.root / "artifacts"))
            pdf, metadata, parsed = directory / "paper.pdf", directory / "record.json", directory / "text.json"
            completed = False
            try:
                size, signature = 0, bytearray()
                checksum = hashlib.sha256()
                with pdf.open("xb") as stream:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise Problem("PDF_TOO_LARGE", 413)
                        if len(signature) < 5:
                            signature.extend(chunk[:5 - len(signature)])
                        if len(signature) == 5 and signature != b"%PDF-":
                            raise Problem("NOT_A_PDF", 422)
                        checksum.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if size < 1024 or signature != b"%PDF-":
                    raise Problem("PDF_INCOMPLETE", 422)
                metadata.write_text(item["record"], encoding="utf-8")
                # Isolate parser failures and never pass cloud/browser credentials
                # to the parsing child process.
                environment = {key: value for key, value in os.environ.items() if key.upper() in {
                    "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LANG", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}}
                environment["PYTHONIOENCODING"] = "utf-8"
                process = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8",
                    str(Path(__file__).with_name("pdf_worker.py")), str(pdf), str(metadata), str(parsed),
                    env=environment, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    result = await asyncio.wait_for(process.wait(), 45)
                except TimeoutError:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise Problem("PDF_PARSE_TIMEOUT", 422) from None
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise
                if result != 0 or not parsed.exists() or parsed.stat().st_size > 32 * 1024 * 1024:
                    raise Problem("PDF_PARSE_FAILED", 422)
                data = json.loads(parsed.read_text(encoding="utf-8"))
                data.update({"sha256": checksum.hexdigest(), "bytes": size})
                parsed.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                metadata.unlink()
                self.store.finish_file(item["id"], f"{directory.name}/paper.pdf", f"{directory.name}/text.json",
                                       data["verification"], browser, lease)
                completed = True
            finally:
                if not completed:
                    # Only a newly created, resolved staging directory is removed.
                    shutil.rmtree(directory)


def make_app(root=None, agent_token=None, port=57133, download_root=None):
    root = Path(root or os.environ.get("LITSCOUT_DOWNLOAD_DATA_DIR") or Path.home() / ".litscout-download")
    store = Store(root)
    token_file = root / "agent-token"
    if agent_token is None:
        if not token_file.exists():
            with token_file.open("x", encoding="utf-8") as file:
                file.write(secrets.token_urlsafe(32))
        agent_token = token_file.read_text(encoding="utf-8").strip()
    if len(agent_token) < 32:
        raise ValueError("Agent token must have at least 32 characters")
    files = Files(store)
    downloads = Downloads(download_root)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield

    app = FastAPI(title="LitScout Download", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.files = store, files
    app.state.downloads = downloads

    @app.exception_handler(Problem)
    async def problem(request, error):
        return JSONResponse({"code": error.code}, status_code=error.status, headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # Reject DNS rebinding and browser-origin requests to the Agent boundary.
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return JSONResponse({"code": "HOST_BLOCKED"}, status_code=403)
        origin = request.headers.get("origin", "")
        browser_path = request.url.path.startswith("/v1/browser/") or request.url.path == "/v1/pair"
        claimed_extension = request.headers.get("x-litscout-extension", "")
        extension_origin = "chrome-extension://" + claimed_extension if claimed_extension else origin
        allowed = bool(EXTENSION_ORIGIN.fullmatch(extension_origin))
        # Background extension fetches may omit Origin. An explicit ID plus
        # nonce/token still binds the peer; a web Origin is always rejected.
        if origin and (not browser_path or not allowed or origin != extension_origin):
            return JSONResponse({"code": "ORIGIN_BLOCKED"}, status_code=403)
        if browser_path and not allowed:
            return JSONResponse({"code": "EXTENSION_ORIGIN_REQUIRED"}, status_code=403)
        request.state.extension_origin = extension_origin
        if request.method == "OPTIONS":
            response = Response(status_code=204)
        else:
            if request.method in {"POST", "PUT"} and not request.url.path.endswith("/pdf"):
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 65536:
                        return JSONResponse({"code": "REQUEST_TOO_LARGE"}, status_code=413)
                request._body = bytes(body)
            response = await call_next(request)
        if allowed and browser_path:
            response.headers.update({"Access-Control-Allow-Origin": extension_origin, "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS", "Access-Control-Allow-Headers": "Authorization, Content-Type, Upload-Lease, X-LitScout-Extension",
                "Access-Control-Allow-Private-Network": "true"})
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def bearer(request):
        auth = request.headers.get("authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else ""

    async def agent(request: Request):
        if not secrets.compare_digest(bearer(request), agent_token):
            raise Problem("AGENT_UNAUTHORIZED", 401)

    async def peer(request: Request):
        return store.browser(bearer(request), request.state.extension_origin.split("//", 1)[1])

    @app.get("/healthz")
    async def health():
        return {"service": "litscout-download", "version": "0.1.4"}

    @app.post("/v1/pair-codes", dependencies=[Depends(agent)])
    async def pair_code():
        return store.pair_code()

    @app.post("/v1/pair")
    async def pair(payload: PairRequest, request: Request):
        return store.pair(payload.code, request.state.extension_origin.split("//", 1)[1], payload.name)

    @app.get("/v1/browsers", dependencies=[Depends(agent)])
    async def browsers():
        return store.browsers()

    @app.post("/v1/browsers/{browser_id}/revoke", dependencies=[Depends(agent)])
    async def revoke(browser_id: str):
        store.revoke(browser_id)
        return {"revoked": True}

    @app.post("/v1/tasks", dependencies=[Depends(agent)], status_code=202)
    async def create(payload: TaskRequest, idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)):
        request = payload.model_dump()
        request_hash = digest(json.dumps(request, sort_keys=True, ensure_ascii=False))
        entries = [{"paper": paper.model_dump(exclude_none=True)} for paper in payload.papers]
        task = store.create(idempotency_key, request_hash, payload.browserId, payload.cloudJobId,
                            entries, payload.taskName.strip(), payload.listId)
        return store.get(task)

    @app.get("/v1/tasks/{task}", dependencies=[Depends(agent)])
    async def status(task: str):
        return store.get(task)

    @app.get("/v1/tasks/{task}/events", dependencies=[Depends(agent)])
    async def events(task: str, after: int = Query(default=0, ge=0)):
        return store.events(task, after)

    @app.get("/v1/lists/{list_id}", dependencies=[Depends(agent)])
    async def paper_list(list_id: str):
        return store.paper_list(list_id)

    @app.post("/v1/tasks/{task}/cancel", dependencies=[Depends(agent)])
    async def cancel(task: str):
        store.cancel(task)
        return store.get(task)

    @app.post("/v1/tasks/{task}/action", dependencies=[Depends(agent)])
    async def task_action(task: str, payload: UserAction):
        store.task_action(task, payload.action, payload.name)
        return store.get(task)

    @app.post("/v1/tasks/{task}/items/{item}/open", dependencies=[Depends(agent)], status_code=202)
    async def request_open(task: str, item: str):
        return store.request_open(task, item)

    @app.get("/v1/tasks/{task}/items/{item}/artifact", dependencies=[Depends(agent)])
    async def artifact(task: str, item: str):
        row = store.item(item)
        if row["job"] != task:
            raise Problem("ITEM_NOT_FOUND", 404)
        if row["state"] != "completed":
            raise Problem("ARTIFACT_NOT_VERIFIED")
        path = store.root / "artifacts" / row["artifact"]
        if not path.is_file():
            raise Problem("ARTIFACT_MISSING", 410)
        return FileResponse(path, media_type="application/pdf", filename="paper.pdf")

    @app.post("/v1/tasks/{task}/items/{item}/export", dependencies=[Depends(agent)])
    async def exported(task: str, item: str, payload: WorkspaceExport):
        row = store.item(item)
        if row["job"] != task:
            raise Problem("ITEM_NOT_FOUND", 404)
        if row["state"] != "completed" or row["verification"] not in {"verified", "user_approved"}:
            raise Problem("ARTIFACT_NOT_VERIFIED")

        def verify_export():
            target = Path(payload.outputPath)
            if (not target.is_absolute() or payload.outputPath.startswith(("\\\\", "//"))
                    or target.suffix.lower() != ".pdf" or target.is_symlink()):
                raise Problem("INVALID_EXPORT_PATH", 422)
            try:
                metadata = json.loads((store.root / "artifacts" / row["parsed"]).read_text(encoding="utf-8"))
                if payload.sha256 != metadata["sha256"] or payload.bytes != metadata["bytes"]:
                    raise Problem("EXPORT_METADATA_MISMATCH", 422)
                if not target.is_file() or target.stat().st_size != payload.bytes:
                    raise Problem("EXPORT_FILE_MISSING_OR_SIZE", 422)
                checksum = hashlib.sha256()
                with target.open("rb") as source:
                    for chunk in iter(lambda: source.read(65536), b""):
                        checksum.update(chunk)
                if checksum.hexdigest() != payload.sha256:
                    raise Problem("EXPORT_HASH_MISMATCH", 422)
                return str(target.resolve())
            except (OSError, ValueError, KeyError):
                raise Problem("EXPORT_FILE_UNAVAILABLE", 422) from None

        target = await asyncio.to_thread(verify_export)
        store.record_export(task, item, {"outputPath": target, "sha256": payload.sha256, "bytes": payload.bytes})
        return {"ok": True, "exportPath": target}

    @app.get("/v1/tasks/{task}/items/{item}/text", dependencies=[Depends(agent)])
    async def text(task: str, item: str, start: int = Query(default=1, ge=1), count: int = Query(default=10, ge=1, le=20)):
        row = store.item(item)
        if row["job"] != task:
            raise Problem("ITEM_NOT_FOUND", 404)
        if row["state"] != "completed":
            raise Problem("ARTIFACT_NOT_VERIFIED")
        path = store.root / "artifacts" / row["parsed"]
        if not path.is_file():
            raise Problem("TEXT_MISSING", 410)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["parserVerification"] = data["verification"]
        data["verification"] = row["verification"]
        data["pages"] = data["pages"][start - 1:start - 1 + count]
        return data

    @app.get("/v1/browser/tasks")
    async def browser_tasks(browser=Depends(peer)):
        return store.browser_tasks(browser["id"])

    @app.post("/v1/browser/tasks/{task}/action")
    async def browser_task_action(task: str, payload: UserAction, browser=Depends(peer)):
        store.task_action(task, payload.action, payload.name, browser["id"])
        return {"ok": True}

    @app.get("/v1/browser/commands")
    async def browser_commands(browser=Depends(peer)):
        return store.open_commands(browser["id"])

    @app.post("/v1/browser/commands/{command}/done")
    async def command_done(command: str, payload: OpenResult, browser=Depends(peer)):
        store.finish_open(command, browser["id"], payload.tabId)
        return {"ok": True}

    @app.post("/v1/browser/claim")
    async def claim(browser=Depends(peer)):
        return {"item": store.claim_browser(browser["id"])}

    @app.post("/v1/browser/items/{item}/import")
    async def import_pdf(item: str, browser=Depends(peer)):
        return {"item": store.claim_import(item, browser["id"])}

    @app.post("/v1/browser/items/{item}/download")
    async def saved_download(item: str, payload: SavedDownload, browser=Depends(peer), lease: str | None = Header(default=None, alias="Upload-Lease")):
        row = store.item(item)
        if row["browser"] != browser["id"]:
            raise Problem("ITEM_NOT_FOUND", 404)
        if row["artifact"] or row["state"] not in {"browser_running", "waiting_user", "needs_browser"}:
            raise Problem("ACTION_NOT_ALLOWED")
        downloads.path(payload.filename)
        if row["state"] != "browser_running":
            lease = store.claim_import(item, browser["id"])["lease"]
        with store.tx() as db:
            store.lease_check(db, item, browser["id"], lease)
            db.execute("UPDATE items SET state='uploading',reason='saved_download_received' WHERE id=?", (item,))
            store.event(db, row["job"], item, "saved_download_received", {"downloadId": payload.downloadId})
        try:
            await files.save(store.item(item), downloads.chunks(payload.filename), browser["id"], lease)
        except (Problem, OSError, ValueError, TimeoutError):
            with store.tx() as db:
                db.execute("UPDATE items SET state='waiting_user',reason='saved_download_import_failed',lease=NULL,lease_until=NULL WHERE id=? AND lease=? AND state='uploading'", (item, lease))
            raise
        return {"ok": True, "state": store.item(item)["state"]}

    @app.post("/v1/browser/items/{item}/report")
    async def report(item: str, payload: BrowserReport, browser=Depends(peer), lease: str = Header(alias="Upload-Lease")):
        store.report(item, browser["id"], lease, payload.state, payload.reason, payload.tabId)
        return {"ok": True}

    @app.put("/v1/browser/items/{item}/pdf")
    async def upload(item: str, request: Request, browser=Depends(peer), lease: str = Header(alias="Upload-Lease")):
        with store.tx() as db:
            store.lease_check(db, item, browser["id"], lease)
            db.execute("UPDATE items SET state='uploading' WHERE id=?", (item,))
        try:
            await files.save(store.item(item), request.stream(), browser["id"], lease)
        except (Problem, OSError, ValueError, TimeoutError):
            with store.tx() as db:
                db.execute("UPDATE items SET state='browser_running' WHERE id=? AND lease=? AND state='uploading'", (item, lease))
            raise
        return {"ok": True, "state": store.item(item)["state"]}

    @app.get("/v1/browser/items/{item}/preview")
    async def preview(item: str, browser=Depends(peer)):
        row = store.item(item)
        if row["browser"] != browser["id"] or not row["artifact"]:
            raise Problem("ITEM_NOT_FOUND", 404)
        return FileResponse(store.root / "artifacts" / row["artifact"], media_type="application/pdf", filename="preview.pdf")

    @app.post("/v1/browser/items/{item}/action")
    async def action(item: str, payload: UserAction, browser=Depends(peer)):
        store.user_action(item, browser["id"], payload.action)
        return {"ok": True}

    return app
