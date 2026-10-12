import asyncio
import json
import time
from pathlib import Path

from scripts.fixture_pdf import pdf_bytes
import httpx
import pytest
from fastapi.testclient import TestClient

from litscout_download.app import make_app
from litscout_download.store import Problem, Store

AGENT = {"Authorization": "Bearer " + "a" * 40}
ORIGIN = "chrome-extension://" + "a" * 32


def papers():
    return [{"id": "paper-1", "title": "Cross disciplinary battery discovery",
             "identifiers": {"doi": "10.1234/example", "arxivId": "1706.03762"},
             "links": {"landingPage": "https://example.org/article"}}]


@pytest.fixture
def client(tmp_path):
    app = make_app(tmp_path, agent_token="a" * 40)
    with TestClient(app, base_url="http://127.0.0.1:57133") as client:
        yield client


def pair(client, origin=ORIGIN):
    code = client.post("/v1/pair-codes", headers=AGENT).json()["code"]
    response = client.post("/v1/pair", headers={"Origin": origin}, json={"code": code, "name": "Campus"})
    assert response.status_code == 200, response.text
    data = response.json()
    return data["browserId"], {"Origin": origin, "Authorization": "Bearer " + data["browserToken"]}, code


def task(client, browser, key="task-test-1234"):
    response = client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": key}, json={"cloudJobId": "job-1", "browserId": browser, "papers": papers()})
    assert response.status_code == 202, response.text
    data = response.json()
    return data


def claim(client, headers):
    response = client.post("/v1/browser/claim", headers=headers)
    assert response.status_code == 200
    item = response.json()["item"]
    return item, {**headers, "Upload-Lease": item["lease"]}


def test_auth_origin_host_and_pairing_boundary(client):
    assert client.get("/v1/browsers").status_code == 401
    assert client.get("/v1/browsers", headers={**AGENT, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/v1/browsers", headers={**AGENT, "Origin": ORIGIN}).status_code == 403
    assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/v1/pair", json={"code": "x" * 30}).status_code == 403
    assert client.post("/v1/browser/claim", headers={"Origin": ORIGIN}).status_code == 401
    browser, headers, code = pair(client)
    replay = client.post("/v1/pair", headers={"Origin": ORIGIN}, json={"code": code})
    assert replay.status_code == 401
    assert client.get("/v1/browser/tasks", headers={**headers, "Origin": "chrome-extension://" + "b" * 32}).status_code == 401
    preflight = client.options("/v1/browser/claim", headers={"Origin": ORIGIN})
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert "browserToken" not in client.get("/v1/browsers", headers=AGENT).text
    background = {"Authorization": headers["Authorization"], "X-LitScout-Extension": "a" * 32}
    assert client.get("/v1/browser/tasks", headers=background).status_code == 200
    assert client.get("/v1/browser/tasks", headers={**background, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/v1/browser/tasks", headers={**background, "X-LitScout-Extension": "b" * 32}).status_code == 401


def test_workspace_export_receipt_integrity_and_persistence(client, tmp_path):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    content = pdf_bytes()
    client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=content).raise_for_status()
    prefix = f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}'
    metadata = client.get(prefix + "/text", headers=AGENT).json()
    target = tmp_path / "workspace.pdf"
    target.write_bytes(content)
    payload = {"outputPath": str(target), "sha256": metadata["sha256"], "bytes": metadata["bytes"]}
    assert client.post(prefix + "/export", headers=headers, json=payload).status_code == 403
    assert client.post(prefix.replace(job["taskId"], "other-task") + "/export", headers=AGENT, json=payload).status_code == 404
    assert client.post(prefix + "/export", headers=AGENT, json={**payload, "sha256": "0" * 64}).status_code == 422
    target.write_bytes(b"x" * len(content))
    assert client.post(prefix + "/export", headers=AGENT, json=payload).json()["code"] == "EXPORT_HASH_MISMATCH"
    target.write_bytes(content)
    for _ in range(2):
        assert client.post(prefix + "/export", headers=AGENT, json=payload).status_code == 200
    store = Store(client.app.state.store.root)
    result = store.get(job["taskId"])
    assert result["progress"]["exported"] == 1
    assert result["items"][0]["exportPath"] == str(target.resolve())
    assert len([event for event in store.events(job["taskId"]) if event["event"] == "workspace_exported"]) == 1
    browser_item = client.get("/v1/browser/tasks", headers=headers).json()[0]["items"][0]
    assert browser_item["exportPath"] == str(target.resolve())
    assert "artifactPath" not in browser_item
    # Recreate the test-owned temporary target to verify missing exports are not advertised.
    target.rename(tmp_path / "moved.pdf")
    assert store.get(job["taskId"])["items"][0]["exportStatus"] == "saved_local"


def test_workspace_export_unverified_and_invalid_paths(client, tmp_path):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    prefix = f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}'
    payload = {"outputPath": str(tmp_path / "paper.pdf"), "sha256": "0" * 64, "bytes": 1024}
    assert client.post(prefix + "/export", headers=AGENT, json=payload).json()["code"] == "ARTIFACT_NOT_VERIFIED"
    client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes()).raise_for_status()
    metadata = client.get(prefix + "/text", headers=AGENT).json()
    for path in ["relative.pdf", "//host/share/paper.pdf", str(tmp_path / "token.txt")]:
        response = client.post(prefix + "/export", headers=AGENT, json={**payload, "outputPath": path,
            "sha256": metadata["sha256"], "bytes": metadata["bytes"]})
        assert response.status_code == 422 and response.json()["code"] == "INVALID_EXPORT_PATH"


def test_task_isolation_idempotency_and_no_arbitrary_urls(client):
    browser, headers, _ = pair(client)
    other, other_headers, _ = pair(client, "chrome-extension://" + "b" * 32)
    result = task(client, browser)
    replay = client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": "task-test-1234"}, json={"cloudJobId": "job-1", "browserId": browser, "papers": papers()})
    assert replay.json()["taskId"] == result["taskId"]
    conflict = client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": "task-test-1234"}, json={"cloudJobId": "job-2", "browserId": browser, "papers": papers()})
    assert conflict.status_code == 409
    arbitrary = client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": "another-test-1234"}, json={"cloudJobId": "job-1", "browserId": browser, "papers": papers(), "url": "https://evil.example"})
    assert arbitrary.status_code == 422
    assert client.post("/v1/browser/claim", headers=other_headers).json()["item"] is None
    item, upload = claim(client, headers)
    assert item["paper"]["identifiers"]["arxivId"] == "1706.03762"
    assert client.post("/v1/browser/claim", headers=headers).json()["item"] is None
    assert client.post(f'/v1/browser/items/{item["itemId"]}/report', headers={**other_headers, "Upload-Lease": item["lease"]}, json={"state": "waiting_user"}).status_code == 404


def test_browser_upload_artifact_text_and_restart_persistence(client):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    response = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes())
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "completed"
    prefix = f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}'
    assert client.get(prefix + "/artifact", headers=AGENT).content[:5] == b"%PDF-"
    text = client.get(prefix + "/text", headers=AGENT, params={"start": 2, "count": 1}).json()
    assert text["pageCount"] == 2
    assert text["pages"][0]["page"] == 2
    assert "Results and discussion" in text["pages"][0]["text"]
    assert text["verification"] == "verified"
    assert len(text["sha256"]) == 64
    assert text["emptyTextPages"] == []
    assert client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes()).status_code == 409
    restarted = Store(client.app.state.store.root)
    restarted.recover()
    assert restarted.get(job["taskId"])["status"] == "completed"
    assert restarted.browser(headers["Authorization"][7:], "a" * 32)["id"] == browser
    assert any(event["event"] == "completed" for event in restarted.events(job["taskId"]))
    assert Path(restarted.get(job["taskId"])["items"][0]["artifactPath"]).exists()


def test_manual_import_exact_item_isolation_busy_and_verified_upload(client):
    browser, headers, _ = pair(client)
    _, other_headers, _ = pair(client, "chrome-extension://" + "b" * 32)
    job = task(client, browser)
    item, upload = claim(client, headers)
    endpoint = f'/v1/browser/items/{item["itemId"]}/import'
    assert client.post(endpoint, headers=other_headers).status_code == 404
    assert client.post(endpoint, headers=headers).status_code == 409
    assert client.post(f'/v1/browser/items/{item["itemId"]}/action', headers=headers, json={"action": "pause"}).status_code == 200
    assert client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes()).status_code == 409
    second = task(client, browser, key="second-import-test")
    assert client.post("/v1/browser/claim", headers=headers).json()["item"] is None
    # Represent an existing pre-0.2 backlog whose public phase already finished.
    second_id = second["items"][0]["id"]
    with client.app.state.store.tx() as db:
        db.execute("UPDATE items SET state='needs_browser' WHERE id=?", (second_id,))
    second_item = client.post(f'/v1/browser/items/{second_id}/import', headers=headers).json()["item"]
    second_upload = {**headers, "Upload-Lease": second_item["lease"]}
    assert client.post(endpoint, headers=headers).json()["code"] == "BROWSER_BUSY"
    assert client.post(f'/v1/browser/items/{item["itemId"]}/action', headers=headers,
                       json={"action": "retry"}).json()["code"] == "BROWSER_BUSY"
    client.post(f'/v1/browser/items/{second_item["itemId"]}/report', headers=second_upload, json={"state": "waiting_user"})
    imported = client.post(endpoint, headers=headers).json()["item"]
    assert imported["itemId"] == item["itemId"]
    result = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers={**headers, "Upload-Lease": imported["lease"]}, content=pdf_bytes())
    assert result.json()["state"] == "completed"
    assert client.post(endpoint, headers=headers).status_code == 409
    assert client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/text', headers=AGENT).json()["verification"] == "verified"


def test_task_pause_resume_name_and_queue_head_are_persistent(client):
    browser, headers, _ = pair(client)
    _, other, _ = pair(client, "chrome-extension://" + "b" * 32)
    job = task(client, browser)
    item, upload = claim(client, headers)
    endpoint = f'/v1/tasks/{job["taskId"]}/action'
    assert client.post(f'/v1/browser/tasks/{job["taskId"]}/action', headers=other, json={"action": "pause"}).status_code == 404
    paused = client.post(endpoint, headers=AGENT, json={"action": "pause"}).json()
    assert paused["paused"] and paused["status"] == "paused"
    assert client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes()).status_code == 409
    client.post(endpoint, headers=AGENT, json={"action": "rename", "name": "生成式 AI 与蛋白质设计"})
    restarted = Store(client.app.state.store.root)
    assert restarted.get(job["taskId"])["taskName"] == "生成式 AI 与蛋白质设计"
    assert restarted.get(job["taskId"])["paused"]
    assert restarted.claim_browser(browser) is None
    resumed = client.post(endpoint, headers=AGENT, json={"action": "resume"}).json()
    assert not resumed["paused"]
    new, fresh = claim(client, headers)
    assert new["itemId"] == item["itemId"]
    client.post(f'/v1/browser/items/{item["itemId"]}/report', headers=fresh, json={"state": "waiting_user", "reason": "publisher_verification"})
    second = task(client, browser, key="blocked-next-task")
    assert client.post("/v1/browser/claim", headers=headers).json()["item"] is None
    assert client.app.state.store.claim_browser(browser) is None
    attention = client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()["attention"]
    assert attention["itemId"] == item["itemId"] and attention["requiresHuman"]
    client.post(endpoint, headers=AGENT, json={"action": "resume"})
    assert client.post("/v1/browser/claim", headers=headers).json()["item"] is None
    client.post(f'/v1/browser/items/{item["itemId"]}/action', headers=headers, json={"action": "skip"})
    assert client.app.state.store.claim_browser(browser)["taskId"] == second["taskId"]


def test_old_blocking_task_remains_visible_beyond_recent_history(client):
    browser, headers, _ = pair(client)
    _, other_headers, _ = pair(client, "chrome-extension://" + "b" * 32)
    job = task(client, browser)
    item, upload = claim(client, headers)
    client.post(f'/v1/browser/items/{item["itemId"]}/report', headers=upload,
                json={"state": "waiting_user", "reason": "publisher_verification"})
    store = client.app.state.store
    records = [{"paper": paper, "publicCandidates": []} for paper in papers()]
    for index in range(21):
        newer = store.create(f"history-task-{index}", str(index), browser, "history-job", records)
        store.cancel(newer)
    visible = client.get("/v1/browser/tasks", headers=headers).json()
    assert len(visible) == 21  # Recent 20 plus the older unfinished queue head.
    assert job["taskId"] in {value["taskId"] for value in visible}
    assert client.get("/v1/browser/tasks", headers=other_headers).json() == []
    assert store.claim_browser(browser) is None


def test_legacy_idempotency_and_agent_open_command_isolation(client):
    from litscout_download.store import digest
    browser, headers, _ = pair(client)
    _, other, _ = pair(client, "chrome-extension://" + "b" * 32)
    payload = {"cloudJobId": "legacy-job", "browserId": browser, "papers": papers()}
    original = client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": "legacy-hash-test"}, json=payload).json()["taskId"]
    response = client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": "legacy-hash-test"}, json=payload)
    assert response.json()["taskId"] == original
    item_id = response.json()["items"][0]["id"]
    open_route = f"/v1/tasks/{original}/items/{item_id}/open"
    assert client.post(open_route, headers=headers).status_code == 403
    command = client.post(open_route, headers=AGENT).json()
    assert client.post(open_route, headers=AGENT).json()["commandId"] == command["commandId"]
    assert client.get("/v1/browser/commands", headers=other).json() == []
    assert client.post(f'/v1/browser/commands/{command["commandId"]}/done', headers=other, json={"tabId": 5}).status_code == 404
    assert client.post(f'/v1/browser/commands/{command["commandId"]}/done', headers=headers, json={"tabId": 5}).status_code == 200
    assert client.get("/v1/browser/commands", headers=headers).json() == []
    assert client.get(f"/v1/tasks/{original}", headers=AGENT).json()["items"][0]["tab"] == 5


def test_task_pause_preserves_an_inflight_upload_and_identity_checks(client):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    with client.app.state.store.tx() as db:
        db.execute("UPDATE items SET state='uploading' WHERE id=?", (item["itemId"],))
    client.post(f'/v1/tasks/{job["taskId"]}/action', headers=AGENT, json={"action": "pause"})
    assert client.app.state.store.item(item["itemId"])["state"] == "uploading"
    result = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes())
    assert result.json()["state"] == "completed"
    state = client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()
    assert state["paused"] and state["items"][0]["agentReadable"]


def test_saved_download_auto_import_and_path_boundary(client, tmp_path):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    client.app.state.downloads.root = download_dir.resolve()
    pdf = download_dir / "paper.pdf"
    pdf.write_bytes(pdf_bytes())
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(pdf_bytes())
    browser, headers, _ = pair(client)
    _, other, _ = pair(client, "chrome-extension://" + "b" * 32)
    job = task(client, browser)
    item, upload = claim(client, headers)
    endpoint = f'/v1/browser/items/{item["itemId"]}/download'
    assert client.post(endpoint, headers=AGENT, json={"downloadId": 5, "filename": str(pdf)}).status_code == 403
    assert client.post(endpoint, headers={**other, "Upload-Lease": item["lease"]}, json={"downloadId": 5, "filename": str(pdf)}).status_code == 404
    rejected = client.post(endpoint, headers=upload, json={"downloadId": 5, "filename": str(outside)})
    assert rejected.json()["code"] == "SAVED_FILE_OUTSIDE_DOWNLOADS"
    result = client.post(endpoint, headers=upload, json={"downloadId": 5, "filename": str(pdf)})
    assert result.json()["state"] == "completed"
    assert client.post(endpoint, headers=upload, json={"downloadId": 5, "filename": str(pdf)}).status_code == 409
    state = client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()
    assert state["progress"]["downloaded"] == state["progress"]["readable"] == 1
    assert state["items"][0]["agentReadable"] and state["items"][0]["fileAvailable"]
    events = client.get(f'/v1/tasks/{job["taskId"]}/events', headers=AGENT).json()
    assert any(event["event"] == "saved_download_received" for event in events)
    assert str(pdf) not in json.dumps(events)


def test_saved_download_after_listener_ends_keeps_manual_pause_and_skips(client, tmp_path):
    client.app.state.downloads.root = tmp_path.resolve()
    pdf = tmp_path / "saved.pdf"
    pdf.write_bytes(pdf_bytes())
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    client.post(f'/v1/tasks/{job["taskId"]}/action', headers=AGENT, json={"action": "pause"})
    endpoint = f'/v1/browser/items/{item["itemId"]}/download'
    result = client.post(endpoint, headers=upload, json={"downloadId": 7, "filename": str(pdf)})
    assert result.json()["state"] == "completed"
    assert client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()["paused"]
    next_job = task(client, browser, key="saved-skip-test")
    next_item = next_job["items"][0]["id"]
    client.post(f'/v1/browser/items/{next_item}/action', headers=headers, json={"action": "skip"})
    assert client.post(f'/v1/browser/items/{next_item}/download', headers=headers, json={"downloadId": 8, "filename": str(pdf)}).status_code == 409


def test_expired_capture_waits_for_user_without_reclaim_loop(client):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, _ = claim(client, headers)
    with client.app.state.store.tx() as db:
        db.execute("UPDATE items SET lease_until=0 WHERE id=?", (item["itemId"],))
    assert client.post("/v1/browser/claim", headers=headers).json()["item"] is None
    state = client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()
    assert state["items"][0]["state"] == "waiting_user"
    assert state["items"][0]["reason"] == "lease_expired"


@pytest.mark.parametrize("body", [b"not a PDF", pdf_bytes(doi="10.9999/unrelated", title="An unrelated paper")])
def test_manual_import_invalid_or_mismatched_pdf_is_not_agent_readable(client, body):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    client.post(f'/v1/browser/items/{item["itemId"]}/report', headers=upload, json={"state": "waiting_user"})
    imported = client.post(f'/v1/browser/items/{item["itemId"]}/import', headers=headers).json()["item"]
    client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers={**headers, "Upload-Lease": imported["lease"]}, content=body)
    assert client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/text', headers=AGENT).status_code == 409


@pytest.mark.parametrize("body", [b"<html>login</html>" + b"x" * 1500, b"%PDF-invalid" + b"x" * 1500, b"%PDF-1.7"])
def test_invalid_pdf_never_becomes_readable(client, body):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    response = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=body)
    assert response.status_code == 422
    assert client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/artifact', headers=AGENT).status_code == 409
    assert not list((client.app.state.store.root / "artifacts").rglob("paper.pdf"))


def test_mismatched_pdf_cannot_be_approved_by_agent_or_user(client):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    response = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload,
                          content=pdf_bytes("Wrong unrelated paper", "10.9999/other"))
    assert response.json()["state"] == "verification_required"
    prefix = f'/v1/browser/items/{item["itemId"]}'
    assert client.get(prefix + "/preview", headers=headers).content[:5] == b"%PDF-"
    assert client.post(prefix + "/action", headers=AGENT, json={"action": "approve"}).status_code == 403
    response = client.post(prefix + "/action", headers=headers, json={"action": "approve"})
    assert response.status_code == 422
    assert response.json()["code"] == "IDENTITY_MISMATCH_CANNOT_APPROVE"
    assert client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/text', headers=AGENT).status_code == 409


def test_mismatched_file_can_be_reimported_without_bypassing_identity(client):
    browser, headers, _ = pair(client)
    _, other, _ = pair(client, "chrome-extension://" + "b" * 32)
    job = task(client, browser)
    item, upload = claim(client, headers)
    prefix = f'/v1/browser/items/{item["itemId"]}'
    wrong = pdf_bytes("Wrong unrelated paper", "10.9999/other")
    client.put(prefix + "/pdf", headers=upload, content=wrong)
    state = client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()
    assert state["attention"]["reason"] == "identity_mismatch"
    assert "import" in state["attention"]["actions"] and "approve" not in state["attention"]["actions"]
    assert client.post(prefix + "/import", headers=AGENT).status_code == 403
    assert client.post(prefix + "/import", headers=other).status_code == 404
    imported = client.post(prefix + "/import", headers=headers).json()["item"]
    fresh_upload = {**headers, "Upload-Lease": imported["lease"]}
    assert client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/text', headers=AGENT).status_code == 409
    assert client.put(prefix + "/pdf", headers=upload, content=pdf_bytes()).status_code == 409
    assert client.put(prefix + "/pdf", headers=fresh_upload, content=b"%PDF-invalid" + b"x" * 1500).status_code == 422
    client.post(prefix + "/report", headers=fresh_upload, json={"state": "waiting_user", "reason": "manual_import_failed"})
    assert client.get(prefix + "/preview", headers=headers).content == wrong
    imported = client.post(prefix + "/import", headers=headers).json()["item"]
    result = client.put(prefix + "/pdf", headers={**headers, "Upload-Lease": imported["lease"]}, content=pdf_bytes())
    assert result.json()["state"] == "completed"
    text = client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/text', headers=AGENT).json()
    assert text["verification"] == "verified"
    assert client.post(prefix + "/import", headers=headers).status_code == 409
    events = client.get(f'/v1/tasks/{job["taskId"]}/events', headers=AGENT).json()
    replacements = [json.loads(event["detail"]) for event in events if event["event"] == "manual_import_started"]
    assert len(replacements) == 2 and all(event["replacesUnverifiedFile"] for event in replacements)


@pytest.mark.parametrize("skip_first", [False, True])
def test_reacquire_reopens_current_article_and_preserves_queue_and_verification(client, skip_first):
    browser, headers, _ = pair(client)
    _, other, _ = pair(client, "chrome-extension://" + "b" * 32)
    job = task(client, browser)
    item, old_upload = claim(client, headers)
    prefix = f'/v1/browser/items/{item["itemId"]}'
    client.post(prefix + "/report", headers=old_upload, json={"state": "browser_running", "tabId": 77})
    client.put(prefix + "/pdf", headers=old_upload, content=pdf_bytes("Wrong unrelated paper", "10.9999/other"))
    rejected = client.app.state.store.item(item["itemId"])
    retained = client.app.state.store.root / "artifacts" / rejected["artifact"]
    following = task(client, browser, key="reacquire-following-task")
    assert client.app.state.store.claim_browser(browser) is None
    assert client.post(prefix + "/action", headers=AGENT, json={"action": "retry"}).status_code == 403
    assert client.post(prefix + "/action", headers=other, json={"action": "retry"}).status_code == 404
    if skip_first:
        assert client.post(prefix + "/action", headers=headers, json={"action": "skip"}).status_code == 200
    assert client.post(prefix + "/action", headers=headers, json={"action": "retry"}).status_code == 200
    reset = client.app.state.store.item(item["itemId"])
    assert reset["state"] == "needs_browser"
    assert reset["artifact"] is None and reset["parsed"] is None and reset["tab"] is None
    assert retained.exists()
    assert client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/text', headers=AGENT).status_code == 409
    fresh, fresh_upload = claim(client, headers)
    assert fresh["itemId"] == item["itemId"] and fresh["tabId"] is None
    assert fresh["tryPublicPdf"] is False
    assert client.app.state.store.claim_browser(browser) is None
    assert client.put(prefix + "/pdf", headers=old_upload, content=pdf_bytes()).status_code == 409
    assert client.put(prefix + "/pdf", headers=fresh_upload, content=pdf_bytes()).json()["state"] == "completed"
    assert client.post(prefix + "/action", headers=headers, json={"action": "retry"}).status_code == 409
    assert client.app.state.store.claim_browser(browser)["taskId"] == following["taskId"]


def test_inconclusive_pdf_requires_explicit_browser_approval(client):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    response = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes("An OCR-poor header", ""))
    assert response.json()["state"] == "verification_required"
    assert client.post(f'/v1/browser/items/{item["itemId"]}/action', headers=headers, json={"action": "approve"}).status_code == 200
    assert client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()["items"][0]["verification"] == "user_approved"


def test_user_resume_and_lease_fencing_on_timeout(client):
    browser, headers, _ = pair(client)
    task(client, browser)
    item, upload = claim(client, headers)
    assert client.post(f'/v1/browser/items/{item["itemId"]}/report', headers=upload,
        json={"state": "waiting_user", "reason": "publisher_verification", "tabId": 17}).status_code == 200
    assert client.post("/v1/browser/claim", headers=headers).json()["item"] is None
    assert client.post(f'/v1/browser/items/{item["itemId"]}/action', headers=headers, json={"action": "capture"}).status_code == 200
    new, fresh = claim(client, headers)
    assert new["tabId"] == 17 and new["assisted"] is True
    assert item["tryPublicPdf"] is True and new["tryPublicPdf"] is False
    assert client.post(f'/v1/browser/items/{item["itemId"]}/report', headers=fresh,
        json={"state": "browser_running", "reason": "manual_capture_active"}).status_code == 200
    assert client.post(f'/v1/browser/items/{item["itemId"]}/report', headers=fresh,
        json={"state": "browser_running", "reason": "lease_renewed"}).status_code == 200
    assert client.app.state.store.item(item["itemId"])["reason"] == "manual_capture_active"
    assert client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes()).status_code == 409
    with client.app.state.store.tx() as db:
        db.execute("UPDATE items SET lease_until=? WHERE id=?", (time.time() - 1, item["itemId"]))
    assert client.post("/v1/browser/claim", headers=headers).json()["item"] is None
    assert client.post(f'/v1/browser/items/{item["itemId"]}/action', headers=headers, json={"action": "resume"}).status_code == 200
    newer, _ = claim(client, headers)
    assert newer["lease"] != new["lease"]


def test_cancel_and_revoke_stop_stale_upload(client):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    assert client.post(f'/v1/tasks/{job["taskId"]}/cancel', headers=AGENT).status_code == 200
    assert client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes()).status_code == 409
    assert client.get(f'/v1/tasks/{job["taskId"]}', headers=AGENT).json()["status"] == "cancelled"
    assert client.post(f"/v1/browsers/{browser}/revoke", headers=AGENT).status_code == 200
    assert client.post("/v1/browser/claim", headers=headers).status_code == 401


def test_request_size_and_missing_list(client):
    assert client.post("/v1/pair-codes", headers=AGENT, content=b"x" * 65537).status_code == 413
    browser, _, _ = pair(client)
    assert client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": "missing-list-test"},
                       json={"cloudJobId": "job-1", "browserId": browser}).status_code == 422


def test_storage_budget_and_ocr_status(client, monkeypatch):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    monkeypatch.setattr("litscout_download.app.DISK_LIMIT", 1024)
    response = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes())
    assert response.status_code == 507
    assert not list((client.app.state.store.root / "artifacts").rglob("paper.pdf"))


def test_ocr_pdf_can_be_preserved_but_has_no_claimed_extracted_text(client):
    browser, headers, _ = pair(client)
    job = task(client, browser)
    item, upload = claim(client, headers)
    body = pdf_bytes(blank=True)
    response = client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=body)
    assert response.json()["state"] == "verification_required"
    assert client.post(f'/v1/browser/items/{item["itemId"]}/action', headers=headers, json={"action": "approve"}).status_code == 200
    text = client.get(f'/v1/tasks/{job["taskId"]}/items/{item["itemId"]}/text', headers=AGENT).json()
    assert text["textStatus"] == "ocr_required"
    assert not text["pages"][0]["text"].strip()
