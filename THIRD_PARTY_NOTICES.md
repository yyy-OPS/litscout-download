# Third-party notices

LitScout Download source, browser extension and Skill are distributed under the root MIT license. Python dependencies are installed separately; their own licenses apply.

| Component | Use | License / upstream |
| --- | --- | --- |
| FastAPI | Local API | MIT — https://github.com/fastapi/fastapi |
| Starlette | ASGI runtime | BSD-3-Clause — https://github.com/Kludex/starlette |
| Pydantic | Input validation | MIT — https://github.com/pydantic/pydantic |
| Uvicorn | Loopback server | BSD-3-Clause — https://github.com/encode/uvicorn |
| pypdf | PDF extraction | BSD-3-Clause — https://github.com/py-pdf/pypdf |
| ReportLab | Synthetic test PDFs only | BSD — https://www.reportlab.com/ |
| Pillow | Test images only | MIT-CMU — https://github.com/python-pillow/Pillow |
| Playwright | Isolated browser tests only | Apache-2.0 — https://github.com/microsoft/playwright-python |
| cryptography | Local test TLS certificate only | Apache-2.0 / BSD — https://github.com/pyca/cryptography |
| pytest / pytest-asyncio / httpx | Tests only | MIT / Apache-2.0 / BSD-3-Clause respectively |

This distribution contains no PyMuPDF binary or source. Test documents are generated locally; no publisher PDF is bundled. Browser binaries and Python dependency wheels are not included. Redistributors who bundle dependencies must preserve those dependencies' license texts and notices.
