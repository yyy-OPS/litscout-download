import copy
import socket

import pytest

from test_workflow import AGENT, client, pair, papers, pdf_bytes, claim
from litscout_download.store import Store


def submit(client, browser, key="list-test-1234", **fields):
    return client.post("/v1/tasks", headers={**AGENT, "Idempotency-Key": key},
                       json={"browserId": browser, "papers": papers(), **fields})


def test_no_cdk_and_no_outbound_network(client, monkeypatch):
    monkeypatch.delenv("LITSCOUT_CDK", raising=False)
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("Unexpected outbound connection"))
    browser, headers, _ = pair(client)
    accepted = submit(client, browser)
    assert accepted.status_code == 202
    task = accepted.json()
    assert task["cloudJobId"] is None and len(task["listId"]) == 36
    item, upload = claim(client, headers)
    assert client.put(f'/v1/browser/items/{item["itemId"]}/pdf', headers=upload, content=pdf_bytes()).status_code == 200
    status = client.get(f'/v1/tasks/{task["taskId"]}', headers=AGENT).json()
    assert status["status"] == "completed"


def test_list_identity_conflict_and_persistence(client):
    browser, _, _ = pair(client)
    accepted = submit(client, browser, listId="my-list", cloudJobId="optional-cloud-link")
    assert accepted.status_code == 202
    original = accepted.json()
    assert submit(client, browser, listId="my-list", cloudJobId="optional-cloud-link").json()["taskId"] == original["taskId"]
    altered = copy.deepcopy(papers())
    altered[0]["title"] = "A different paper"
    assert submit(client, browser, "other-key-1234", listId="my-list", papers=altered).json()["code"] == "LIST_ID_CONFLICT"
    retry = submit(client, browser, "explicit-new-run", listId="my-list", cloudJobId="optional-cloud-link")
    assert retry.status_code == 202 and retry.json()["taskId"] != original["taskId"]
    listing = Store(client.app.state.store.root).paper_list("my-list")
    assert listing["papers"][0]["title"] == papers()[0]["title"]
    assert listing["cloudJobId"] == "optional-cloud-link"
    assert client.get("/v1/lists/my-list").status_code == 401
    assert client.get("/v1/lists/my-list", headers=AGENT).json() == listing


@pytest.mark.parametrize("paper", [
    {"title": "Title only"}, {"title": " ", "identifiers": {"doi": "10.1234/ok"}},
    {"title": "Invalid DOI", "identifiers": {"doi": "not-doi"}},
    {"title": "Internal host", "links": {"pdfUrl": "https://127.0.0.1/a.pdf"}},
    {"title": "Insecure URL", "links": {"landingPage": "http://example.org/a"}},
    {"title": "Credentials in URL", "links": {"pdfUrl": "https://user:pass@example.org/a.pdf"}},
    {"title": "Internal domain", "links": {"landingPage": "https://server.internal/a"}},
])
def test_invalid_paper_rejected_atomically(client, paper):
    browser, headers, _ = pair(client)
    assert submit(client, browser, papers=[paper]).status_code == 422
    assert client.get("/v1/browser/tasks", headers=headers).json() == []


def test_minimal_doi_paper_and_duplicate_identity(client):
    browser, _, _ = pair(client)
    paper = {"title": "A DOI-only submitted publication", "identifiers": {"doi": "https://doi.org/10.1234/Example"}}
    assert submit(client, browser, papers=[paper, paper]).status_code == 422
    response = submit(client, browser, papers=[paper])
    assert response.status_code == 202
    record = response.json()["items"][0]["paperRecord"]
    assert record["id"] == "paper-001" and record["links"]["doiUrl"] == "https://doi.org/10.1234/example"


def test_twenty_limit_and_id_collision(client):
    browser, _, _ = pair(client)
    values = [{"title": f"Unique paper title {i}", "links": {"pdfUrl": f"https://example.org/{i}.pdf"}} for i in range(21)]
    assert submit(client, browser, papers=values).status_code == 422
    assert submit(client, browser, papers=values[:20]).status_code == 202
    values[0]["id"] = "paper-002"
    assert submit(client, browser, "other-duplicate-id", papers=values[:2]).status_code == 422


def test_source_url_only_remains_openable_in_sidepanel(client):
    browser, _, _ = pair(client)
    response = submit(client, browser, papers=[{"title": "Repository sourced publication",
        "links": {"sourceUrls": {"repository": "https://example.org/article"}}}])
    assert response.status_code == 202
    assert response.json()["items"][0]["articleUrl"] == "https://example.org/article"
