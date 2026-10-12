from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

TERMINAL = {"completed", "failed", "no_entitlement", "cancelled"}


class Problem(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def uid():
    return str(uuid.uuid4())


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        with self.tx() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS pair_codes(hash TEXT PRIMARY KEY, expires REAL);
                CREATE TABLE IF NOT EXISTS browsers(id TEXT PRIMARY KEY, extension TEXT NOT NULL,
                    name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, seen REAL, revoked INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL,
                    request_hash TEXT NOT NULL, browser TEXT NOT NULL REFERENCES browsers(id), cloud_job TEXT, list_id TEXT NOT NULL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_lists(id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                    cloud_job TEXT, papers TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id),
                    paper TEXT NOT NULL, record TEXT NOT NULL,
                    state TEXT NOT NULL, reason TEXT, tab INTEGER, lease TEXT, lease_until REAL,
                    artifact TEXT, parsed TEXT, verification TEXT);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL,
                    item TEXT, event TEXT NOT NULL, detail TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS task_controls(job TEXT PRIMARY KEY REFERENCES jobs(id),
                    name TEXT NOT NULL DEFAULT '', paused INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS browser_commands(id TEXT PRIMARY KEY, item TEXT NOT NULL REFERENCES items(id),
                    browser TEXT NOT NULL REFERENCES browsers(id), state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS item_job ON items(job);
                CREATE INDEX IF NOT EXISTS event_job ON events(job, seq);
            ''')

    @contextmanager
    def tx(self):
        with self.lock:
            db = sqlite3.connect(self.root / "state.sqlite3", timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def event(self, db, job, item, event, detail=None):
        db.execute("INSERT INTO events(job,item,event,detail,created) VALUES(?,?,?,?,?)",
                   (job, item, event, json.dumps(detail or {}, ensure_ascii=False), time.time()))

    def pair_code(self):
        code = secrets.token_urlsafe(24)
        with self.tx() as db:
            db.execute("DELETE FROM pair_codes WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO pair_codes VALUES(?,?)", (digest(code), time.time() + 300))
        return {"code": code, "expiresInSeconds": 300}

    def pair(self, code, extension, name):
        token, browser_id = secrets.token_urlsafe(32), uid()
        with self.tx() as db:
            row = db.execute("SELECT * FROM pair_codes WHERE hash=?", (digest(code),)).fetchone()
            if not row or row["expires"] < time.time():
                raise Problem("PAIR_CODE_INVALID", 401)
            db.execute("DELETE FROM pair_codes WHERE hash=?", (digest(code),))
            db.execute("INSERT INTO browsers(id,extension,name,token_hash,seen) VALUES(?,?,?,?,?)",
                       (browser_id, extension, name, digest(token), time.time()))
        return {"browserId": browser_id, "browserToken": token}

    def browser(self, token, extension):
        with self.tx() as db:
            row = db.execute("SELECT * FROM browsers WHERE token_hash=? AND extension=? AND revoked=0",
                             (digest(token), extension)).fetchone()
            if not row:
                raise Problem("BROWSER_UNAUTHORIZED", 401)
            db.execute("UPDATE browsers SET seen=? WHERE id=?", (time.time(), row["id"]))
            return dict(row)

    def browsers(self):
        with self.tx() as db:
            return [dict(row) for row in db.execute("SELECT id,extension,name,seen,revoked FROM browsers ORDER BY seen DESC")]

    def revoke(self, browser_id):
        with self.tx() as db:
            if not db.execute("UPDATE browsers SET revoked=1 WHERE id=?", (browser_id,)).rowcount:
                raise Problem("BROWSER_NOT_FOUND", 404)
            rows = db.execute("SELECT i.* FROM items i JOIN jobs j ON i.job=j.id WHERE j.browser=? AND i.state IN ('browser_running','uploading')", (browser_id,)).fetchall()
            for row in rows:
                db.execute("UPDATE items SET state='needs_browser',lease=NULL,lease_until=NULL,reason='browser_revoked' WHERE id=?", (row["id"],))
                self.event(db, row["job"], row["id"], "browser_revoked")

    def create(self, idem, request_hash, browser, cloud_job, records, name="", list_id=None):
        with self.tx() as db:
            existing = db.execute("SELECT * FROM jobs WHERE idem=?", (idem,)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Problem("IDEMPOTENCY_CONFLICT")
                return existing["id"]
            peer = db.execute("SELECT id FROM browsers WHERE id=? AND revoked=0", (browser,)).fetchone()
            if not peer:
                raise Problem("BROWSER_NOT_FOUND", 404)
            if db.execute("SELECT count(*) FROM items WHERE state NOT IN ('completed','failed','no_entitlement','cancelled')").fetchone()[0] + len(records) > 100:
                raise Problem("QUEUE_FULL", 429)
            list_id = list_id or uid()
            papers = [entry["paper"] for entry in records]
            serialized = json.dumps(papers, sort_keys=True, ensure_ascii=False)
            content_hash = digest(json.dumps([cloud_job, papers], sort_keys=True, ensure_ascii=False))
            previous = db.execute("SELECT content_hash FROM paper_lists WHERE id=?", (list_id,)).fetchone()
            if previous and previous["content_hash"] != content_hash:
                raise Problem("LIST_ID_CONFLICT")
            db.execute("INSERT OR IGNORE INTO paper_lists VALUES(?,?,?,?,?)",
                       (list_id, content_hash, cloud_job, serialized, time.time()))
            job_id = uid()
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (job_id, idem, request_hash, browser, cloud_job, list_id, time.time()))
            db.execute("INSERT INTO task_controls(job,name) VALUES(?,?)", (job_id, name))
            for entry in records:
                paper = entry["paper"]
                db.execute("INSERT INTO items(id,job,paper,record,state) VALUES(?,?,?,?,?)",
                           (uid(), job_id, paper["id"], json.dumps(paper, ensure_ascii=False),
                            "needs_browser"))
            self.event(db, job_id, None, "task_created", {"count": len(records), "listId": list_id, "cloudJobId": cloud_job})
            return job_id

    def get(self, job_id):
        with self.tx() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise Problem("TASK_NOT_FOUND", 404)
            browser = db.execute("SELECT seen,revoked FROM browsers WHERE id=?", (job["browser"],)).fetchone()
            rows = db.execute("SELECT * FROM items WHERE job=? ORDER BY rowid", (job_id,)).fetchall()
            control = db.execute("SELECT * FROM task_controls WHERE job=?", (job_id,)).fetchone()
            exports = {}
            for event in db.execute("SELECT item,detail,created FROM events WHERE job=? AND event='workspace_exported' ORDER BY seq", (job_id,)):
                exports[event["item"]] = {**json.loads(event["detail"]), "exportedAt": event["created"]}
            items = []
            for row in rows:
                value = dict(row)
                for field in ("lease", "lease_until", "record", "parsed"):
                    value.pop(field, None)
                value.pop("artifact", None)
                paper = json.loads(row["record"])
                value["title"] = paper.get("title")
                value["identifiers"] = paper.get("identifiers") or {}
                value["year"] = paper.get("year")
                value["paperRecord"] = paper
                links = paper.get("links") or {}
                value["articleUrl"] = (links.get("doiUrl") or links.get("landingPage") or links.get("arxivUrl")
                                       or next(iter((links.get("sourceUrls") or {}).values()), None) or links.get("pdfUrl"))
                value["fileAvailable"] = bool(row["artifact"])
                value["agentReadable"] = row["state"] == "completed" and bool(row["artifact"])
                value["exportStatus"] = "saved_local" if row["artifact"] else "pending"
                receipt = exports.get(row["id"])
                if receipt and value["agentReadable"] and Path(receipt["outputPath"]).is_file():
                    value.update(exportStatus="exported", exportPath=receipt["outputPath"], exportedAt=receipt["exportedAt"])
                value["downloadStatus"] = "downloaded" if row["artifact"] else "captured" if row["reason"] == "pdf_captured" or row["state"] == "uploading" else "pending"
                if row["state"] == "verification_required" and row["verification"] == "mismatch":
                    value["reason"] = "identity_mismatch"
                if row["artifact"] and row["state"] == "completed":
                    value["artifactPath"] = str(self.root / "artifacts" / row["artifact"])
                items.append(value)
            states = {row["state"] for row in rows}
            if states <= TERMINAL:
                completed = sum(row["state"] == "completed" for row in rows)
                status = "completed" if completed == len(rows) else "partial" if completed else "failed"
                if states == {"cancelled"}:
                    status = "cancelled"
            elif control and control["paused"]:
                status = "paused"
            elif states & {"waiting_user", "verification_required"}:
                status = "waiting_user"
            else:
                status = "running"
            blocked = next((item for item in items if item["state"] not in TERMINAL), None)
            attention = None
            if blocked and blocked["state"] in {"waiting_user", "verification_required"}:
                attention = {"itemId": blocked["id"], "title": blocked["title"], "reason": blocked["reason"],
                             "tabId": blocked["tab"], "requiresHuman": True,
                             "actions": ["preview", "approve", "retry", "import", "skip"] if blocked["state"] == "verification_required" and blocked["verification"] != "mismatch" else ["preview", "retry", "import", "skip"] if blocked["state"] == "verification_required" else ["open_article", "resume", "capture", "retry", "import", "skip"]}
            counts = {state: sum(item["state"] == state for item in items) for state in states}
            return {"taskId": job["id"], "taskName": (control["name"] if control else "") or f"全文 · {items[0]['title'][:70]}" if items else "全文任务",
                    "listId": job["list_id"], "cloudJobId": job["cloud_job"], "browserId": job["browser"], "status": status,
                    "paused": bool(control and control["paused"]), "pauseReason": "user_paused" if control and control["paused"] else (attention or {}).get("reason"),
                    "attention": attention, "counts": counts,
                    "progress": {"total": len(items), "downloaded": sum(item["fileAvailable"] for item in items), "readable": counts.get("completed", 0), "exported": sum(item["exportStatus"] == "exported" for item in items), "finished": sum(item["state"] in TERMINAL for item in items)},
                    "browserOnline": bool(browser and not browser["revoked"] and time.time() - browser["seen"] < 90),
                    "createdAt": job["created"], "items": items}

    def record_export(self, job, item, detail):
        with self.tx() as db:
            row = db.execute("SELECT state,verification FROM items WHERE id=? AND job=?", (item, job)).fetchone()
            if not row:
                raise Problem("ITEM_NOT_FOUND", 404)
            if row["state"] != "completed" or row["verification"] not in {"verified", "user_approved"}:
                raise Problem("ARTIFACT_NOT_VERIFIED")
            latest = db.execute("SELECT detail FROM events WHERE job=? AND item=? AND event='workspace_exported' ORDER BY seq DESC LIMIT 1", (job, item)).fetchone()
            if not latest or json.loads(latest["detail"]) != detail:
                self.event(db, job, item, "workspace_exported", detail)

    def paper_list(self, list_id):
        with self.tx() as db:
            row = db.execute("SELECT * FROM paper_lists WHERE id=?", (list_id,)).fetchone()
            if not row:
                raise Problem("LIST_NOT_FOUND", 404)
            return {"schemaVersion": 1, "listId": row["id"], "cloudJobId": row["cloud_job"],
                    "createdAt": row["created"], "papers": json.loads(row["papers"])}

    def events(self, job, after=0):
        self.get(job)
        with self.tx() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE job=? AND seq>? ORDER BY seq LIMIT 200", (job, after))]

    def item(self, item_id):
        with self.tx() as db:
            row = db.execute("SELECT i.*,j.browser FROM items i JOIN jobs j ON i.job=j.id WHERE i.id=?", (item_id,)).fetchone()
            if not row:
                raise Problem("ITEM_NOT_FOUND", 404)
            return dict(row)

    def lease_check(self, db, item_id, browser, lease):
        row = db.execute("SELECT i.*,j.browser FROM items i JOIN jobs j ON i.job=j.id WHERE i.id=?", (item_id,)).fetchone()
        if not row or row["browser"] != browser:
            raise Problem("ITEM_NOT_FOUND", 404)
        if row["lease"] != lease or (row["lease_until"] or 0) <= time.time() or row["state"] not in {"browser_running", "uploading"}:
            raise Problem("LEASE_STALE")
        return row

    def recover(self):
        with self.tx() as db:
            # Keep active browser leases valid across service restarts.
            self.expire_leases(db)

    def expire_leases(self, db):
        rows = db.execute("SELECT * FROM items WHERE state IN ('browser_running','uploading') AND lease_until<?", (time.time(),)).fetchall()
        for row in rows:
            db.execute("UPDATE items SET state='waiting_user',reason='lease_expired',lease=NULL,lease_until=NULL WHERE id=?", (row["id"],))
            self.event(db, row["job"], row["id"], "lease_expired")

    def claim_browser(self, browser):
        with self.tx() as db:
            self.expire_leases(db)
            # One live article per paired profile keeps publisher sessions sequential.
            active = db.execute("SELECT 1 FROM items i JOIN jobs j ON i.job=j.id WHERE j.browser=? AND i.state IN ('browser_running','uploading')", (browser,)).fetchone()
            if active:
                return None
            row = self.queue_head(db, browser)
            if not row or row["state"] != "needs_browser":
                return None
            lease = secrets.token_urlsafe(24)
            # Public retrieval is a first-attempt optimization. Retries and
            # resumed browser work must stay on the publisher article path.
            try_public_pdf = not db.execute(
                "SELECT 1 FROM events WHERE job=? AND item=? AND event='browser_claimed' LIMIT 1",
                (row["job"], row["id"])).fetchone()
            db.execute("UPDATE items SET state='browser_running',lease=?,lease_until=? WHERE id=?", (lease, time.time() + 150, row["id"]))
            self.event(db, row["job"], row["id"], "browser_claimed")
            return {"taskId": row["job"], "itemId": row["id"], "paper": json.loads(row["record"]),
                    "tabId": row["tab"], "assisted": row["reason"] == "manual_capture", "lease": lease,
                    "tryPublicPdf": try_public_pdf, "leaseSeconds": 150, "maxBytes": 32 * 1024 * 1024}

    def queue_head(self, db, browser):
        row = db.execute("""SELECT i.*,COALESCE(c.paused,0) AS task_paused FROM items i
            JOIN jobs j ON i.job=j.id LEFT JOIN task_controls c ON c.job=j.id
            WHERE j.browser=? AND i.state NOT IN ('completed','failed','no_entitlement','cancelled')
            ORDER BY j.created,j.rowid,i.rowid LIMIT 1""", (browser,)).fetchone()
        return row if row and not row["task_paused"] else None

    def task_action(self, job_id, action, name="", browser=None):
        with self.tx() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or browser is not None and job["browser"] != browser:
                raise Problem("TASK_NOT_FOUND", 404)
            db.execute("INSERT OR IGNORE INTO task_controls(job) VALUES(?)", (job_id,))
            if action == "rename":
                if not name.strip() or len(name.strip()) > 120:
                    raise Problem("INVALID_TASK_NAME", 422)
                db.execute("UPDATE task_controls SET name=? WHERE job=?", (name.strip(), job_id))
            elif action == "pause":
                db.execute("UPDATE task_controls SET paused=1 WHERE job=?", (job_id,))
                # Preserve in-flight durable uploads; stop new work and capture.
                db.execute("UPDATE items SET state='waiting_user',reason='task_paused',lease=NULL,lease_until=NULL WHERE job=? AND state='browser_running'", (job_id,))
            elif action == "resume":
                db.execute("UPDATE task_controls SET paused=0 WHERE job=?", (job_id,))
                db.execute("UPDATE items SET state='needs_browser',reason=NULL WHERE job=? AND state='waiting_user' AND reason IN ('task_paused','user_paused')", (job_id,))
            else:
                raise Problem("ACTION_NOT_ALLOWED")
            self.event(db, job_id, None, "task_" + action, {"actor": "browser" if browser else "agent"})

    def report(self, item, browser, lease, state, reason, tab=None):
        if state not in {"browser_running", "waiting_user", "no_entitlement", "failed"}:
            raise Problem("INVALID_STATE", 422)
        with self.tx() as db:
            row = self.lease_check(db, item, browser, lease)
            if state == "browser_running" and reason == "lease_renewed":
                db.execute("UPDATE items SET lease_until=? WHERE id=?", (time.time() + 150, item))
                return
            db.execute("UPDATE items SET state=?,reason=?,tab=COALESCE(?,tab),lease_until=? WHERE id=?",
                       (state, reason, tab, time.time() + 150 if state == "browser_running" else 0, item))
            self.event(db, row["job"], item, state, {"reason": reason})

    def claim_import(self, item_id, browser):
        with self.tx() as db:
            self.expire_leases(db)
            row = db.execute("SELECT i.* FROM items i JOIN jobs j ON i.job=j.id WHERE i.id=? AND j.browser=?", (item_id, browser)).fetchone()
            if not row:
                raise Problem("ITEM_NOT_FOUND", 404)
            if row["state"] not in {"waiting_user", "needs_browser", "cancelled", "verification_required"} or (
                row["artifact"] and row["verification"] in {"verified", "user_approved"}
            ):
                raise Problem("ACTION_NOT_ALLOWED")
            active = db.execute("SELECT 1 FROM items i JOIN jobs j ON i.job=j.id WHERE j.browser=? AND i.state IN ('browser_running','uploading')", (browser,)).fetchone()
            if active:
                raise Problem("BROWSER_BUSY")
            lease = secrets.token_urlsafe(24)
            db.execute("UPDATE items SET state='browser_running',reason='manual_import_started',lease=?,lease_until=? WHERE id=?", (lease, time.time() + 150, item_id))
            # Keep the earlier file for diagnosis; a new upload still needs its
            # own lease and identity verification before Agent access.
            self.event(db, row["job"], item_id, "manual_import_started",
                       {"replacesUnverifiedFile": bool(row["artifact"]), "previousVerification": row["verification"]})
            return {"taskId": row["job"], "itemId": item_id, "paper": json.loads(row["record"]),
                    "tabId": row["tab"], "lease": lease, "maxBytes": 32 * 1024 * 1024}

    def finish_file(self, item_id, artifact, parsed, verification, browser, lease):
        with self.tx() as db:
            row = self.lease_check(db, item_id, browser, lease)
            state = "completed" if verification == "verified" else "verification_required"
            db.execute("UPDATE items SET state=?,artifact=?,parsed=?,verification=?,reason=?,lease=NULL,lease_until=NULL WHERE id=?",
                       (state, artifact, parsed, verification, None if state == "completed" else "identity_requires_user", item_id))
            self.event(db, row["job"], item_id, state, {"verification": verification})

    def user_action(self, item_id, browser, action):
        with self.tx() as db:
            row = db.execute("SELECT i.*,j.browser FROM items i JOIN jobs j ON i.job=j.id WHERE i.id=? AND j.browser=?", (item_id, browser)).fetchone()
            if not row:
                raise Problem("ITEM_NOT_FOUND", 404)
            if action == "approve" and row["state"] == "verification_required" and row["artifact"]:
                if row["verification"] == "mismatch":
                    raise Problem("IDENTITY_MISMATCH_CANNOT_APPROVE", 422)
                state, verification = "completed", "user_approved"
            elif action in {"resume", "capture"} and row["state"] == "waiting_user":
                state, verification = "needs_browser", row["verification"]
            elif action == "retry" and row["state"] in {"waiting_user", "verification_required", "cancelled"}:
                active = db.execute("SELECT 1 FROM items i JOIN jobs j ON i.job=j.id WHERE j.browser=? AND i.state IN ('browser_running','uploading')", (browser,)).fetchone()
                if active:
                    raise Problem("BROWSER_BUSY")
                state, verification = "needs_browser", None
            elif action == "pause" and row["state"] == "browser_running":
                state, verification = "waiting_user", row["verification"]
            elif action == "skip" and row["state"] not in TERMINAL:
                state, verification = "cancelled", row["verification"]
            else:
                raise Problem("ACTION_NOT_ALLOWED")
            db.execute("UPDATE items SET state=?,verification=?,reason=?,lease=NULL,lease_until=NULL WHERE id=?",
                       (state, verification, {"capture": "manual_capture", "pause": "user_paused"}.get(action), item_id))
            if action == "retry":
                # Reopen the target article, rather than reusing a wrong PDF tab.
                # Old files stay on disk; only the pending item's pointers reset.
                db.execute("UPDATE items SET tab=NULL,artifact=NULL,parsed=NULL WHERE id=?", (item_id,))
            if action in {"resume", "capture", "retry"}:
                db.execute("UPDATE task_controls SET paused=0 WHERE job=?", (row["job"],))
            self.event(db, row["job"], item_id, "user_" + action,
                       {"previousVerification": row["verification"], "previousFileRetained": bool(row["artifact"])} if action == "retry" else None)

    def cancel(self, job_id):
        self.get(job_id)
        with self.tx() as db:
            db.execute("UPDATE items SET state='cancelled',lease=NULL,lease_until=NULL WHERE job=? AND state NOT IN ('completed','failed','no_entitlement','cancelled')", (job_id,))
            self.event(db, job_id, None, "task_cancelled")

    def browser_tasks(self, browser):
        with self.tx() as db:
            # Keep an old blocking task visible even when newer history exists.
            jobs = [row[0] for row in db.execute("""SELECT j.id FROM jobs j WHERE j.browser=? AND (
                j.id IN (SELECT id FROM jobs WHERE browser=? ORDER BY created DESC LIMIT 20)
                OR EXISTS (SELECT 1 FROM items i WHERE i.job=j.id
                    AND i.state NOT IN ('completed','failed','no_entitlement','cancelled')))
                ORDER BY j.created DESC,j.rowid DESC""", (browser, browser))]
        tasks = [self.get(job) for job in jobs]
        for task in tasks:
            for item in task["items"]:
                item.pop("artifactPath", None)
        return tasks

    def request_open(self, job_id, item_id):
        with self.tx() as db:
            row = db.execute("SELECT i.*,j.browser FROM items i JOIN jobs j ON i.job=j.id WHERE i.id=? AND i.job=?", (item_id, job_id)).fetchone()
            if not row:
                raise Problem("ITEM_NOT_FOUND", 404)
            existing = db.execute("SELECT id FROM browser_commands WHERE item=? AND state='pending'", (item_id,)).fetchone()
            if existing:
                return {"commandId": existing["id"], "state": "pending"}
            if db.execute("SELECT count(*) FROM browser_commands WHERE browser=? AND state='pending'", (row["browser"],)).fetchone()[0] >= 20:
                raise Problem("QUEUE_FULL", 429)
            command_id = uid()
            db.execute("INSERT INTO browser_commands(id,item,browser,created) VALUES(?,?,?,?)", (command_id, item_id, row["browser"], time.time()))
            self.event(db, job_id, item_id, "agent_open_requested", {"commandId": command_id})
            return {"commandId": command_id, "state": "pending"}

    def open_commands(self, browser):
        with self.tx() as db:
            rows = db.execute("SELECT c.id,i.tab,i.record FROM browser_commands c JOIN items i ON c.item=i.id WHERE c.browser=? AND c.state='pending' ORDER BY c.created LIMIT 20", (browser,)).fetchall()
            return [{"commandId": row["id"], "tabId": row["tab"], "paper": json.loads(row["record"])} for row in rows]

    def finish_open(self, command, browser, tab=None):
        with self.tx() as db:
            row = db.execute("SELECT c.*,i.job FROM browser_commands c JOIN items i ON c.item=i.id WHERE c.id=? AND c.browser=?", (command, browser)).fetchone()
            if not row:
                raise Problem("COMMAND_NOT_FOUND", 404)
            if row["state"] != "pending":
                return
            db.execute("UPDATE browser_commands SET state=? WHERE id=?", ("completed" if tab is not None else "failed", command))
            if tab is not None:
                db.execute("UPDATE items SET tab=? WHERE id=?", (tab, row["item"]))
            self.event(db, row["job"], row["item"], "agent_open_completed" if tab is not None else "agent_open_failed", {"commandId": command})
