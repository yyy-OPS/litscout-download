"""Isolated bounded PDF extraction; invoked without cloud/Agent credentials."""
import json
import re
import sys
import unicodedata
from pathlib import Path

from pypdf import PdfReader


def limit_resources():
    """Bound parser memory without changing host-wide settings or permissions."""
    limit = 512 * 1024 * 1024
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
            ("Flags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t), ("MaxWorkingSet", ctypes.c_size_t),
            ("ActiveProcesses", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
            ("Priority", wintypes.DWORD), ("Scheduling", wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperations", "WriteOperations", "OtherOperations", "ReadBytes", "WriteBytes", "OtherBytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Counters), ("ProcessMemory", ctypes.c_size_t),
            ("JobMemory", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.Flags = 0x100 | 0x2000  # PROCESS_MEMORY and KILL_ON_JOB_CLOSE
    info.ProcessMemory = limit
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise RuntimeError("PDF_RESOURCE_LIMIT_UNAVAILABLE")
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise RuntimeError("PDF_RESOURCE_LIMIT_UNAVAILABLE")
    # Keep the handle open for the process lifetime; closing it kills the parser.
    return handle


def normalized(value):
    return "".join(char for char in unicodedata.normalize("NFKC", value).lower() if char.isalnum())


def extract(path, paper):
    with open(path, "rb") as source:
        doc = PdfReader(source)
        if doc.is_encrypted or not 0 < len(doc.pages) <= 5000:
            raise ValueError("PDF_ENCRYPTED_OR_PAGE_LIMIT")
        pages, chars, invalid_unicode_pages = [], 0, []
        for number, page in enumerate(doc.pages):
            text = page.extract_text() or ""
            # Some PDF font maps yield UTF-16 surrogate pairs or lone surrogates.
            # Preserve valid pairs; disclose malformed glyphs instead of failing
            # UTF-8 serialization after the complete PDF has been extracted.
            if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
                encoded = text.encode("utf-16-le", errors="surrogatepass")
                try:
                    text = encoded.decode("utf-16-le")
                except UnicodeDecodeError:
                    text = encoded.decode("utf-16-le", errors="replace")
                    invalid_unicode_pages.append(number + 1)
            if len(text) > 200000:
                raise ValueError("PDF_PAGE_TEXT_LIMIT")
            chars += len(text)
            if chars > 8_000_000:
                raise ValueError("PDF_TEXT_LIMIT")
            pages.append({"page": number + 1, "text": text})
    # Identity comes from the article header, not a DOI in its bibliography.
    first = pages[0]["text"].split("References")[0][:10000]
    expected = normalized(paper.get("title", ""))
    title_match = len(expected) >= 12 and expected in normalized(first)
    doi = str((paper.get("identifiers") or {}).get("doi") or "").lower().removeprefix("https://doi.org/")
    dois = re.findall(r"10\.\d{4,9}/[^\s<>\"]+", first.lower())
    doi_match = bool(doi) and any(value.rstrip(".,;)") == doi for value in dois)
    # A title match alone is valid for DOI-less preprints. Missing identity is
    # inconclusive, never silently treated as verified.
    verification = "verified" if title_match and (not doi or doi_match or not dois) else "inconclusive"
    if doi and dois and not doi_match and not title_match:
        verification = "mismatch"
    empty_pages = [page["page"] for page in pages if not page["text"].strip()]
    text_state = ("ocr_required" if sum(len(page["text"].strip()) for page in pages) < 100
                  else "partial_text" if empty_pages or invalid_unicode_pages else "text_available")
    return {"verification": verification, "pageCount": len(pages), "textStatus": text_state,
            "pages": pages, "emptyTextPages": empty_pages, "invalidUnicodePages": invalid_unicode_pages,
            "identity": {"titleMatched": title_match, "doiMatched": doi_match}}


if __name__ == "__main__":
    try:
        _resource_handle = limit_resources()
        paper = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        result = extract(sys.argv[1], paper)
        Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        sys.exit(2)
