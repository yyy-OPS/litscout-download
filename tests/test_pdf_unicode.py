import json
from types import SimpleNamespace

from litscout_download import pdf_worker


def test_pdf_font_surrogates_serialize_and_disclose_text_loss(tmp_path, monkeypatch):
    title = "Engineering operator learning example"
    texts = [title + "\nMath: \ud835\udc00 and 𝛼" + " Engineering methods and experimental results." * 5, "Broken glyph: \ud835 end"]
    pages = [SimpleNamespace(extract_text=lambda value=value: value) for value in texts]
    monkeypatch.setattr(pdf_worker, "PdfReader", lambda source: SimpleNamespace(is_encrypted=False, pages=pages))
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"%PDF-")
    result = pdf_worker.extract(source, {"title": title})
    serialized = json.dumps(result, ensure_ascii=False).encode("utf-8")
    restored = json.loads(serialized)
    assert "𝐀 and 𝛼" in restored["pages"][0]["text"]
    assert "\ufffd" in restored["pages"][1]["text"]
    assert restored["invalidUnicodePages"] == [2]
    assert restored["verification"] == "verified"
    assert restored["textStatus"] == "partial_text"
