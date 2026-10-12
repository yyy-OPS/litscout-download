"""Real Chromium extension smoke using isolated profiles and local fixtures only."""
import asyncio
import base64
import json
import os
import ssl
import sys
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fixture_pdf import pdf_bytes
import httpx
import uvicorn
from playwright.async_api import async_playwright
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from litscout_download.app import make_app
from litscout_download.store import Problem

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "isolated-browser-smoke-token-0123456789"
PORT = 57139


def fixture_papers(job):
    page = {"fixture-captcha": "captcha", "fixture-notification": "captcha", "fixture-manual": "manual", "fixture-uncertain": "uncertain",
            "fixture-attachment": "attachment", "fixture-import": "manual", "fixture-direct": "pdf/no-extension",
            "fixture-saved-blob": "saved-blob", "fixture-blocked-batch": "captcha", "fixture-retry": "manual"}.get(job, "article")
    papers = [{"id": "fixture-paper", "title": "Cross disciplinary battery discovery",
               "identifiers": {"doi": "10.1234/example"}, "links": {
                   "doiUrl": f"https://fixture.litscout.test/{page}",
                   "landingPage": f"https://fixture.litscout.test/{page}"}}]
    if job == "fixture-direct-attachment":
        papers[0]["links"] = {"pdfUrl": "https://fixture.litscout.test/attachment-direct"}
        papers[0]["identifiers"] = {}
    if job in {"fixture-public-fallback", "fixture-retry"}:
        papers[0]["links"]["pdfUrl"] = "https://fixture.litscout.test/attachment-direct"
    if job == "fixture-blocked-batch":
        papers.append({"id": "fixture-paper-2", "title": "Independent engineering battery discovery",
                       "identifiers": {"doi": "10.1234/second"}, "links": {"pdfUrl": "https://fixture.litscout.test/second.pdf"}})
    return papers


async def main():
    # Remove cloud credentials only from this isolated test process.
    for name in list(os.environ):
        if "CDK" in name.upper() or name == "LITSCOUT_API_BASE_URL":
            del os.environ[name]
    with tempfile.TemporaryDirectory(prefix="litscout-browser-smoke-") as directory:
        downloads_path = Path(directory) / "downloads"
        downloads_path.mkdir()
        app = make_app(Path(directory) / "data", agent_token=TOKEN, port=PORT, download_root=downloads_path)
        (Path(directory) / "data" / "agent-token").write_text(TOKEN, encoding="utf-8")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            assert server.started
            pdf = pdf_bytes()
            uncertain_pdf = pdf_bytes(image_title=True)
            second_pdf = pdf_bytes("Independent engineering battery discovery", "10.1234/second")
            batch_pdfs = {f"/batch-{i}.pdf": pdf_bytes(f"Engineering battery experiment number {i:02d}", f"10.1234/batch-{i}") for i in range(20)}
            class Publisher(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = pdf if self.path in {"/paper.pdf", "/pdf/no-extension"} else b'''<!doctype html><html><head>
                        <meta name="citation_title" content="Cross disciplinary battery discovery">
                        <meta name="citation_doi" content="10.1234/example">
                        <meta name="citation_pdf_url" content="https://fixture.litscout.test/paper.pdf"></head>
                        <body><h1>Cross disciplinary battery discovery</h1><a href="/paper.pdf">Download PDF</a></body></html>'''
                    if self.path == "/captcha":
                        body = b'<html><body><p>Fixture CAPTCHA: verify you are human</p><a href="/article">Simulate user verification completion</a></body></html>'
                    if self.path == "/manual":
                        body = b'<html><head><meta name="citation_title" content="Cross disciplinary battery discovery"></head><body><h1>Cross disciplinary battery discovery</h1><button onclick="location.href=\'/paper.pdf\'">Read full article</button></body></html>'
                    if self.path == "/uncertain":
                        body = b'<html><head><meta name="citation_title" content="Cross disciplinary battery discovery"><meta name="citation_pdf_url" content="https://fixture.litscout.test/uncertain.pdf"></head><body><h1>Cross disciplinary battery discovery</h1><a href="/uncertain.pdf">Download PDF</a></body></html>'
                    if self.path == "/uncertain.pdf":
                        body = uncertain_pdf
                    if self.path == "/attachment":
                        body = b'''<html><head><meta name="citation_title" content="Cross disciplinary battery discovery"></head>
                        <body><button onclick="fetch('/export?id=7').then(r=>r.blob()).then(b=>{const a=document.createElement('a');a.href=window.URL.createObjectURL(b);a.download='article.pdf';a.click();})">Download PDF</button></body></html>'''
                    if self.path == "/export?id=7":
                        body = pdf
                    if self.path == "/saved-blob":
                        body = ('''<html><head><meta name="citation_title" content="Cross disciplinary battery discovery"></head>
                        <body><button onclick="window.fixtureClicked=true;const bytes=Uint8Array.from(atob('%s'),c=>c.charCodeAt(0));const a=document.createElement('a');a.href=window.URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));a.download='saved-article.pdf';a.click();">Download PDF</button></body></html>''' % base64.b64encode(pdf).decode()).encode()
                    if self.path == "/second.pdf":
                        body = second_pdf
                    if self.path in batch_pdfs:
                        body = batch_pdfs[self.path]
                    if self.path == "/attachment-direct":
                        if "publisher_access=yes" not in self.headers.get("Cookie", ""):
                            self.send_response(401)
                            self.end_headers()
                            self.wfile.write(b"Sign in to access this document")
                            return
                        body = pdf
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream" if self.path in {"/export?id=7", "/attachment-direct"} else "application/pdf" if self.path.endswith(".pdf") or self.path == "/pdf/no-extension" else "text/html")
                    if self.path in {"/export?id=7", "/attachment-direct"}:
                        self.send_header("Content-Disposition", 'attachment; filename="article.pdf"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def log_message(self, *args):
                    pass
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture.litscout.test")])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName("fixture.litscout.test")]), critical=False)
                .sign(key, hashes.SHA256()))
            cert_path, key_path = Path(directory) / "fixture.crt", Path(directory) / "fixture.key"
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            publisher = ThreadingHTTPServer(("127.0.0.1", 0), Publisher)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert_path, key_path)
            publisher.socket = tls.wrap_socket(publisher.socket, server_side=True)
            publisher_thread = threading.Thread(target=publisher.serve_forever, daemon=True)
            publisher_thread.start()
            # This harness intentionally restarts the server; do not carry an
            # HTTP keep-alive socket from the old process into the new one.
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", headers={"Authorization": "Bearer " + TOKEN},
                                         trust_env=False, timeout=15, limits=httpx.Limits(max_keepalive_connections=0)) as client:
                code = (await client.post("/v1/pair-codes")).json()["code"]
                async with async_playwright() as playwright:
                    # This test never opens the user's normal profile/session.
                    context = await playwright.chromium.launch_persistent_context(str(Path(directory) / "profile"),
                        channel="chromium", executable_path=os.environ.get("LITSCOUT_SMOKE_CHROMIUM"), headless=True,
                        args=[f"--disable-extensions-except={ROOT / 'apps/browser-extension'}",
                        f"--load-extension={ROOT / 'apps/browser-extension'}", "--ignore-certificate-errors", "--no-proxy-server", "--disable-background-networking",
                        f"--host-resolver-rules=MAP fixture.litscout.test 127.0.0.1:{publisher.server_port}, MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"])
                    try:
                        context.on("page", lambda publisher_page: publisher_page.on("pageerror", lambda error: print("Isolated page script error:", str(error), flush=True)))
                        worker = context.service_workers[0] if context.service_workers else await context.wait_for_event("serviceworker", timeout=15000)
                        extension = worker.url.split("/")[2]
                        # Prove cloud unreachability from the actual extension worker.
                        cloud_accessible = await worker.evaluate("""async () => {
                            try { await fetch('https://litscout.zeabur.app/healthz', {signal: AbortSignal.timeout(5000)}); return true; }
                            catch { return false; }
                        }""")
                        assert cloud_accessible is False
                        print("Isolated browser: no CDK; cloud DNS blocked", flush=True)
                        await worker.evaluate("""() => {
                            globalThis.smokeDownloadEvents = [];
                            globalThis.smokeNotifications = [];
                            globalThis.smokeUnhandledRejections = [];
                            addEventListener('unhandledrejection', event => {
                                globalThis.smokeUnhandledRejections.push(String(event.reason));
                            });
                            const create = chrome.notifications.create.bind(chrome.notifications);
                            chrome.notifications.create = async (id, options) => {
                                try {
                                    const result = await create(id, options);
                                    globalThis.smokeNotifications.push({id, success: true, title: options.title});
                                    return result;
                                } catch (error) {
                                    globalThis.smokeNotifications.push({id, success: false, error: String(error)});
                                    throw error;
                                }
                            };
                            chrome.debugger.onEvent.addListener((source, method) => { if (/download/i.test(method)) globalThis.smokeDownloadEvents.push(method); });
                            chrome.runtime.onMessage.addListener(message => { if (message.type === 'BOUND_BLOB_DOWNLOAD') globalThis.smokeDownloadEvents.push('BOUND_BLOB_DOWNLOAD'); });
                        }""")
                        page = await context.new_page()
                        await page.goto(f"chrome-extension://{extension}/index.html")
                        assert await worker.evaluate("async () => (await chrome.sidePanel.getOptions({})).path") == "index.html"
                        assert await worker.evaluate("async () => (await chrome.sidePanel.getPanelBehavior()).openPanelOnActionClick")
                        cdp = await context.new_cdp_session(page)
                        await cdp.send("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(downloads_path), "eventsEnabled": True})
                        await page.locator("#endpoint").fill("http://127.0.0.1:57133")
                        await page.locator("#code").fill(f"litscout-download:v1:{PORT}:{code}")
                        await page.locator("#pairForm button[type=submit]").click()
                        try:
                            await page.locator("#browserId").wait_for(state="visible", timeout=15000)
                        except Exception:
                            print("Extension UI error:", await page.locator("#notice").inner_text())
                            raise
                        browser = await page.locator("#browserId").inner_text()
                        assert await page.locator("#endpoint").input_value() == f"http://127.0.0.1:{PORT}"
                        assert len(browser) == 36, await page.locator("#notice").inner_text()
                        assert await page.locator("a.brand").get_attribute("href") == "https://litscout.zeabur.app/"
                        assert await page.locator("a.brand").get_attribute("rel") == "noopener noreferrer"
                        # No extension UI is open: native background alerts must still work.
                        await page.goto("about:blank")
                        alert_job = await client.post("/v1/tasks", headers={"Idempotency-Key": "notification-smoke-1234"},
                            json={"listId": "fixture-notification", "papers": fixture_papers("fixture-notification"), "browserId": browser, "taskName": "提醒能力 · 隔离测试"})
                        assert alert_job.status_code == 202, alert_job.text
                        alert_task = alert_job.json()["taskId"]
                        await worker.evaluate("() => chrome.alarms.create('litscout-poll', {when: Date.now() + 100, periodInMinutes: 0.5})")
                        deadline = time.monotonic() + 105
                        while time.monotonic() < deadline:
                            alert_state = (await client.get(f"/v1/tasks/{alert_task}")).json()
                            signals = await worker.evaluate("""async () => ({
                                notifications: globalThis.smokeNotifications, badge: await chrome.action.getBadgeText({}),
                                permission: await chrome.notifications.getPermissionLevel(),
                                state: (await chrome.storage.local.get('taskNotificationState')).taskNotificationState,
                                status: (await chrome.storage.local.get('taskNotificationStatus')).taskNotificationStatus
                            })""")
                            if alert_state["status"] == "waiting_user" and any(value["id"] == "litscout-attention-" + alert_task and value["success"] for value in signals["notifications"]):
                                break
                            await asyncio.sleep(0.3)
                        assert alert_state["status"] == "waiting_user" and signals["badge"] == "1", (alert_state, signals)
                        assert any(value["id"] == "litscout-attention-" + alert_task and value["success"] for value in signals["notifications"]), signals
                        assert any(value["id"] == "litscout-received-" + alert_task and value["success"] for value in signals["notifications"]), signals
                        assert page.url == "about:blank"
                        assert await worker.evaluate("() => chrome.notifications.onClicked.hasListeners()")
                        await page.goto(f"chrome-extension://{extension}/index.html")
                        await page.locator("#taskSelect").select_option(alert_task)
                        assert await page.locator("#desktopNotifications").is_checked()
                        await page.locator("#testNotification").click()
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            if await worker.evaluate("() => globalThis.smokeNotifications.some(value => value.id === 'litscout-test' && value.success)"):
                                break
                            await asyncio.sleep(0.1)
                        assert await worker.evaluate("() => globalThis.smokeNotifications.some(value => value.id === 'litscout-test' && value.success)")
                        await page.get_by_role("button", name="跳过此篇", exact=True).click()
                        await page.locator("#desktopNotifications").uncheck()
                        await worker.evaluate("async () => { for (const id of Object.keys(await chrome.notifications.getAll())) await chrome.notifications.clear(id); }")
                        response = await client.post("/v1/tasks", headers={"Idempotency-Key": "browser-live-smoke-1234"},
                                                     json={"listId": "fixture-job", "papers": fixture_papers("fixture-job"), "browserId": browser})
                        assert response.status_code == 202, response.text
                        task = response.json()["taskId"]
                        await asyncio.sleep(1.2)
                        await page.locator("#refresh").click()
                        until = time.monotonic() + 105
                        while time.monotonic() < until:
                            result = (await client.get(f"/v1/tasks/{task}")).json()
                            if result["status"] in {"completed", "failed", "waiting_user"}:
                                break
                            await asyncio.sleep(0.5)
                        if result["status"] != "completed":
                            print("Browser capture error:", await worker.evaluate("async () => (await chrome.storage.local.get('lastError')).lastError"))
                        assert result["status"] == "completed", result
                        item = result["items"][0]["id"]
                        text = (await client.get(f"/v1/tasks/{task}/items/{item}/text")).json()
                        assert text["pageCount"] == 2 and text["verification"] == "verified"
                        assert "Results and discussion" in text["pages"][1]["text"]
                        client_environment = {key: value for key, value in os.environ.items() if key.upper() in {
                            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}}
                        client_environment.update({"LITSCOUT_DOWNLOAD_DATA_DIR": str(Path(directory) / "data"),
                                                   "LITSCOUT_DOWNLOAD_API_BASE_URL": f"http://127.0.0.1:{PORT}"})
                        process = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8",
                            str(ROOT / "skills/litscout-browser-download/scripts/fulltext.py"), "text", task, item, "--start", "2", "--count", "1",
                            env=client_environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        stdout, stderr = await process.communicate()
                        assert process.returncode == 0, stderr.decode()
                        skill_result = json.loads(stdout.decode("utf-8"))
                        assert skill_result["pages"][0]["page"] == 2
                        async def create_task(list_id):
                            accepted = await client.post("/v1/tasks", headers={"Idempotency-Key": list_id + "-browser-live-smoke"},
                                                         json={"listId": list_id, "papers": fixture_papers(list_id), "browserId": browser, "taskName": list_id})
                            assert accepted.status_code == 202, accepted.text
                            await asyncio.sleep(1.2)
                            await page.locator("#refresh").click()
                            await page.locator("#taskSelect").select_option(accepted.json()["taskId"])
                            return accepted.json()["taskId"]

                        async def wait_task(task_id, expected):
                            deadline = time.monotonic() + 240
                            diagnostic = False
                            while time.monotonic() < deadline:
                                data = (await client.get(f"/v1/tasks/{task_id}")).json()
                                if data["status"] == expected:
                                    print(f"Browser fixture: {data['taskName']} -> {expected}", flush=True)
                                    return data
                                if data["taskName"] == "fixture-import" and expected == "completed":
                                    notice = await page.locator("#notice").inner_text()
                                    assert not notice or "正在导入" in notice, notice
                                if data["taskName"] == "fixture-saved-blob" and data["status"] == "waiting_user" and not diagnostic:
                                    diagnostic = True
                                    print("Isolated saved-download diagnostic:", await worker.evaluate("""async () => ({
                                      lastError: (await chrome.storage.local.get('lastError')).lastError,
                                      pendingCount: Object.keys((await chrome.storage.local.get('pendingDownloads')).pendingDownloads || {}).length,
                                      events: globalThis.smokeDownloadEvents,
                                      downloads: (await chrome.downloads.search({})).map(d => ({id:d.id,state:d.state,filename:d.filename,mime:d.mime,referrer:d.referrer,startTime:d.startTime}))
                                    })"""), flush=True)
                                    publisher_page = next(tab for tab in context.pages if tab.url.endswith("/saved-blob"))
                                    print("Isolated page diagnostic:", await publisher_page.evaluate("() => ({buttons:document.querySelectorAll('button').length,hook:!!globalThis.__litscoutBoundBlobHook})"), flush=True)
                                await asyncio.sleep(0.3)
                            raise AssertionError(data)

                        challenge = await create_task("fixture-captcha")
                        pending = await wait_task(challenge, "waiting_user")
                        assert pending["items"][0]["reason"] == "publisher_verification"
                        # This is a local fixture link, never a real CAPTCHA solver.
                        publisher_page = next(tab for tab in reversed(context.pages) if tab.url.endswith("/captcha"))
                        await publisher_page.get_by_text("Simulate user verification completion").click()
                        await page.locator("#refresh").click()
                        await page.get_by_role("button", name="验证完成，继续", exact=True).click()
                        await wait_task(challenge, "completed")

                        manual = await create_task("fixture-manual")
                        pending = await wait_task(manual, "waiting_user")
                        assert pending["items"][0]["reason"] == "no_visible_pdf"
                        await page.locator("#refresh").click()
                        await page.get_by_role("button", name="监听手动下载", exact=True).click()
                        until = time.monotonic() + 15
                        while time.monotonic() < until:
                            active = (await client.get(f"/v1/tasks/{manual}")).json()
                            if active["items"][0]["reason"] == "manual_capture_active":
                                break
                            await asyncio.sleep(0.1)
                        assert active["items"][0]["reason"] == "manual_capture_active", active
                        publisher_page = next(tab for tab in context.pages if tab.url.endswith("/manual"))
                        await publisher_page.get_by_role("button", name="Read full article").click()
                        await wait_task(manual, "completed")
                        uncertain = await create_task("fixture-uncertain")
                        pending = await wait_task(uncertain, "waiting_user")
                        assert pending["items"][0]["verification"] == "inconclusive"
                        await page.locator("#refresh").click()
                        async with context.expect_page() as preview_event:
                            await page.get_by_role("button", name="预览 PDF", exact=True).click()
                        preview = await preview_event.value
                        await preview.wait_for_load_state()
                        # Observe the DOM attribute directly; do not evaluate a
                        # string in an extension page with a strict CSP.
                        await preview.locator('iframe[src^="blob:"]').wait_for(state="attached", timeout=15000)
                        assert "本机" in await preview.locator("#status").inner_text()
                        if os.environ.get("LITSCOUT_SMOKE_SCREENSHOT"):
                            await asyncio.sleep(0.5)
                            await preview.screenshot(path=str(Path(os.environ["LITSCOUT_SMOKE_SCREENSHOT"]).with_name("pdf-preview.png")), full_page=True)
                        page.once("dialog", lambda dialog: dialog.accept())
                        await page.get_by_role("button", name="确认是当前文献", exact=True).click()
                        approved = await wait_task(uncertain, "completed")
                        assert approved["items"][0]["verification"] == "user_approved"
                        approved_text = (await client.get(f'/v1/tasks/{uncertain}/items/{approved["items"][0]["id"]}/text')).json()
                        assert approved_text["verification"] == "user_approved"
                        assert approved_text["parserVerification"] == "inconclusive"

                        attachment = await create_task("fixture-attachment")
                        await wait_task(attachment, "completed")
                        direct = await create_task("fixture-direct")
                        await wait_task(direct, "completed")
                        fallback = await create_task("fixture-public-fallback")
                        await wait_task(fallback, "completed")
                        fallback_events = (await client.get(f"/v1/tasks/{fallback}/events")).json()
                        assert any(json.loads(event["detail"]).get("reason") == "public_pdf_fallback" for event in fallback_events), fallback_events
                        assert any(json.loads(event["detail"]).get("reason") == "pdf_captured" for event in fallback_events), fallback_events
                        retry_task = await create_task("fixture-retry")
                        retry_before = await wait_task(retry_task, "waiting_user")
                        await page.locator("#refresh").click()
                        await page.get_by_role("button", name="重新获取 PDF", exact=True).click()
                        retry_item = retry_before["items"][0]["id"]
                        until = time.monotonic() + 30
                        while time.monotonic() < until:
                            retry_after = (await client.get(f"/v1/tasks/{retry_task}")).json()
                            if retry_after["status"] == "waiting_user" and retry_after["items"][0]["tab"] != retry_before["items"][0]["tab"]:
                                break
                            await asyncio.sleep(0.1)
                        assert retry_after["items"][0]["id"] == retry_item
                        assert retry_after["status"] == "waiting_user"
                        assert retry_after["items"][0]["tab"] != retry_before["items"][0]["tab"]
                        retry_events = (await client.get(f"/v1/tasks/{retry_task}/events")).json()
                        assert sum(json.loads(event["detail"]).get("reason") == "public_pdf_attempt" for event in retry_events) == 1, retry_events
                        assert sum(json.loads(event["detail"]).get("reason") == "article_opened" for event in retry_events) == 2, retry_events
                        await page.locator("#refresh").click()
                        await page.get_by_role("button", name="跳过此篇", exact=True).click()
                        await context.add_cookies([{"name": "publisher_access", "value": "yes", "url": "https://fixture.litscout.test"}])
                        direct_attachment = await create_task("fixture-direct-attachment")
                        await wait_task(direct_attachment, "completed")
                        attachment_events = (await client.get(f"/v1/tasks/{direct_attachment}/events")).json()
                        assert any(json.loads(event["detail"]).get("reason") == "public_pdf_fallback" for event in attachment_events), attachment_events
                        assert any(json.loads(event["detail"]).get("reason") == "pdf_captured" for event in attachment_events), attachment_events
                        assert not any(event["event"] == "saved_download_received" for event in attachment_events), attachment_events
                        imported = await create_task("fixture-import")
                        import_pending = await wait_task(imported, "waiting_user")
                        # Select an already saved PDF through the extension UI.
                        await page.locator("#refresh").click()
                        card = page.locator("article").filter(has=page.locator(".state-detail", has_text="未识别到 PDF 入口")).last
                        await card.get_by_role("button", name="导入已下载 PDF").click()
                        await page.locator("#pdfFile").set_input_files({"name": "article.pdf", "mimeType": "application/pdf", "buffer": pdf})
                        imported_result = await wait_task(imported, "completed")
                        assert imported_result["items"][0]["id"] == import_pending["items"][0]["id"]
                        assert imported_result["items"][0]["verification"] == "verified"

                        saved_blob = await create_task("fixture-saved-blob")
                        saved_result = await wait_task(saved_blob, "completed")
                        saved_events = (await client.get(f"/v1/tasks/{saved_blob}/events")).json()
                        assert any(event["event"] == "saved_download_received" for event in saved_events), saved_events
                        assert saved_result["progress"]["downloaded"] == 1
                        assert (downloads_path / "saved-article.pdf").is_file()

                        blocked = await create_task("fixture-blocked-batch")
                        blocked_state = await wait_task(blocked, "waiting_user")
                        blocked_item = blocked_state["items"][0]["id"]
                        assert blocked_state["items"][1]["state"] == "needs_browser"
                        later_task = await create_task("fixture-later-task")
                        later_state = (await client.get(f"/v1/tasks/{later_task}")).json()
                        assert later_state["items"][0]["state"] == "needs_browser"
                        assert await page.locator("#queueBlocker").is_visible()
                        await page.locator("#showQueueHead").click()
                        assert await page.locator("#taskSelect").input_value() == blocked
                        opened = await client.post(f'/v1/tasks/{blocked}/items/{blocked_item}/open')
                        assert opened.status_code == 202
                        await page.locator("#refresh").click()
                        await asyncio.sleep(0.4)
                        assert any(event["event"] == "agent_open_completed" for event in (await client.get(f"/v1/tasks/{blocked}/events")).json())
                        await page.locator("#toggleTask").click()
                        paused = await wait_task(blocked, "paused")
                        assert paused["paused"]
                        await page.locator("#renameTask").click()
                        await page.locator("#renameInput").fill("跨学科文献 · 暂停协作测试")
                        await page.locator("#renameForm button[type=submit]").click()
                        assert (await client.get(f"/v1/tasks/{blocked}")).json()["taskName"] == "跨学科文献 · 暂停协作测试"
                        await page.locator("#toggleTask").click()
                        resumed = await wait_task(blocked, "waiting_user")
                        assert not resumed["paused"] and resumed["items"][1]["state"] == "needs_browser"
                        await page.get_by_role("button", name="跳过此篇", exact=True).first.click()
                        await wait_task(blocked, "partial")
                        await wait_task(later_task, "completed")
                        await page.locator("#refresh").click()
                        await page.locator("#taskSelect").select_option(later_task)
                        await page.get_by_role("button", name="已下载", exact=True).click()
                        assert await page.locator("article").count() == 1
                        assert "本机已保存" in await page.locator("article .state").inner_text()
                        exported_item = (await client.get(f"/v1/tasks/{later_task}")).json()["items"][0]["id"]
                        export_prefix = f"/v1/tasks/{later_task}/items/{exported_item}"
                        export_metadata = (await client.get(export_prefix + "/text")).json()
                        workspace = Path(directory) / "workspace"
                        workspace.mkdir()
                        target = workspace / "Cross disciplinary battery discovery.pdf"
                        target.write_bytes((await client.get(export_prefix + "/artifact")).content)
                        receipt = await client.post(export_prefix + "/export", json={"outputPath": str(target),
                            "sha256": export_metadata["sha256"], "bytes": export_metadata["bytes"]})
                        assert receipt.status_code == 200, receipt.text
                        await page.locator("#refresh").click()
                        await page.locator("article .state").filter(has_text="已导出到工作目录").wait_for(timeout=10000)
                        assert "已导出到工作目录" in await page.locator("article .state").inner_text()
                        assert await page.locator("article .state-detail").inner_text() == str(target.resolve())
                        assert await page.get_by_role("button", name="复制文件路径", exact=True).is_visible()
                        assert "1 篇已导出" in await page.locator("#readableCount").inner_text()
                        assert await page.locator("#otherTasks").get_attribute("open") is None
                        # Exercise the distributed Skill against a full 20-paper queue.
                        list_file = Path(directory) / "engineering-list.json"
                        list_file.write_text(json.dumps({"schemaVersion": 1, "listId": "engineering-batch-20",
                            "papers": [{"id": f"engineering-{i}", "title": f"Engineering battery experiment number {i:02d}",
                                "identifiers": {"doi": f"10.1234/batch-{i}"},
                                "links": {"pdfUrl": f"https://fixture.litscout.test/batch-{i}.pdf"}} for i in range(20)]}), encoding="utf-8")

                        async def skill(*arguments):
                            process = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8",
                                str(ROOT / "skills/litscout-browser-download/scripts/fulltext.py"), *arguments,
                                env=client_environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                            stdout, stderr = await process.communicate()
                            assert process.returncode == 0, stderr.decode()
                            return json.loads(stdout.decode("utf-8"))

                        batch = await skill("submit", "--list", str(list_file), "--browser", browser,
                            "--key", "engineering-batch-smoke", "--name", "Engineering batch 20")
                        replay = await skill("submit", "--list", str(list_file), "--browser", browser,
                            "--key", "engineering-batch-smoke", "--name", "Engineering batch 20")
                        assert replay["taskId"] == batch["taskId"]
                        listing = await skill("list", batch["listId"])
                        assert len(listing["papers"]) == 20 and listing["cloudJobId"] is None
                        await page.locator("#refresh").click()
                        await page.locator("#taskSelect").select_option(batch["taskId"])
                        await page.locator(".paper-list summary").click()
                        assert "engineering-batch-20" in await page.locator("#paperListJson").inner_text()
                        assert "Engineering battery experiment number 19" in await page.locator("#paperListJson").inner_text()
                        # Stop and restart the actual companion while the browser is working.
                        server.should_exit = True
                        await asyncio.to_thread(thread.join, 10)
                        assert not thread.is_alive()
                        await asyncio.sleep(1.2)
                        app = make_app(Path(directory) / "data", agent_token=TOKEN, port=PORT, download_root=downloads_path)
                        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error", access_log=False))
                        thread = threading.Thread(target=server.run, daemon=True)
                        thread.start()
                        for _ in range(100):
                            if server.started:
                                break
                            await asyncio.sleep(0.05)
                        assert server.started
                        await page.locator("#refresh").click()
                        batch_state = await wait_task(batch["taskId"], "completed")
                        assert batch_state["progress"]["readable"] == 20
                        batch_events = (await client.get(f"/v1/tasks/{batch['taskId']}/events")).json()
                        assert any(json.loads(event["detail"]).get("reason") == "public_pdf_captured" for event in batch_events)
                        exported = await skill("export", batch["taskId"], "--output-dir", str(workspace / "batch"))
                        assert exported["exported"] == 20 and not exported["errors"]
                        manifest = json.loads(Path(exported["manifestPath"]).read_text(encoding="utf-8"))
                        assert manifest["listId"] == batch["listId"]
                        assert len({entry["sha256"] for entry in manifest["items"]}) == 20
                        assert all(Path(entry["exportPath"]).is_file() for entry in manifest["items"])
                        assert (await client.get(f"/v1/tasks/{batch['taskId']}")).json()["progress"]["exported"] == 20
                        print("Skill: 20 distinct PDFs verified and exported after companion restart", flush=True)
                        for width in (320, 420, 600):
                            await page.set_viewport_size({"width": width, "height": 900})
                            assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                        if os.environ.get("LITSCOUT_SMOKE_SCREENSHOT"):
                            await page.locator("#refresh").click()
                            await page.set_viewport_size({"width": 420, "height": 900})
                            screenshot = Path(os.environ["LITSCOUT_SMOKE_SCREENSHOT"])
                            screenshot.parent.mkdir(parents=True, exist_ok=True)
                            await page.screenshot(path=str(screenshot), full_page=True)
                        rejections = await worker.evaluate("() => globalThis.smokeUnhandledRejections")
                        assert rejections == [], rejections
                        report = {"status": "passed", "checks": ["real_mv3_pair", "browser_uuid",
                            "async_task", "browser_capture", "local_file", "identity", "two_page_text",
                            "challenge_pause", "user_resume_same_tab", "assisted_manual_capture", "skill_client_read",
                            "identity_handoff", "pdf_preview", "explicit_user_approval", "attachment_fetch_blob_download",
                            "already_open_pdf_reload", "public_failure_returns_to_article", "retry_article_without_public_replay", "first_attachment_response_before_save", "saved_pdf_import_exact_item", "approved_identity_text_consistency",
                            "native_side_panel_configuration", "saved_blob_download_auto_import", "current_paper_blocks_next",
                            "task_pause_resume", "task_rename", "agent_open_article", "task_selection_and_folded_history", "responsive_sidepanel",
                            "later_task_waits_for_current", "queue_head_navigation", "local_save_vs_workspace_export",
                            "export_path_and_copy_button", "background_receipt_with_panel_closed", "background_human_attention_badge",
                            "native_notification_api", "notification_test_and_toggle", "notification_click_listener",
                            "no_unhandled_worker_rejections", "no_cdk_cloud_unreachable", "brand_link", "code_only_pairing_custom_port",
                            "paper_list_visible", "skill_list_submit_and_replay", "companion_restart",
                            "batch_20_distinct_verified_pdfs", "skill_export_20_with_receipts", "public_pdf_without_cookies", "extensionless_pdf_mime_capture"],
                            "pageCount": text["pageCount"], "batch": {"submitted": 20, "verified": 20, "exported": 20},
                            "cloudAccessible": cloud_accessible, "cdkConfigured": False}
                        print(json.dumps(report))
                        if os.environ.get("LITSCOUT_SMOKE_REPORT"):
                            report_path = Path(os.environ["LITSCOUT_SMOKE_REPORT"])
                            report_path.parent.mkdir(parents=True, exist_ok=True)
                            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    finally:
                        await context.close()
            publisher.shutdown()
            publisher.server_close()
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 10)


if __name__ == "__main__":
    asyncio.run(main())
