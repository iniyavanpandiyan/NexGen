"""Orchestrator-managed OCR for the NexGen pipeline.

Replaces the Tesseract fallback with the Unlimited-OCR model served by the
GPU orchestrator (gpu-orchestrator). The orchestrator swaps the single RTX
3060 between Gemma (normal profile, default) and OCR (on-demand profile).

Typical flow:
    from pipeline.lib.orch_ocr import transcribe_pdf

    text = transcribe_pdf("/path/to/book.pdf")          # acquires + releases
    # or use the context manager to OCR many books in one swap:
    with OcrSession() as ocr:
        t1 = ocr.transcribe_page_img(png_bytes_or_path)
        t2 = ocr.transcribe_page_img(other_png)

Every call bumps the orchestrator's idle timer, so a long OCR run won't be
auto-restored to the Gemma profile mid-way. On any failure (orchestrator
down, swap error, model error) the functions raise -- callers fall back to
pypdf text.
"""
import base64
import json
import os
import re
import time
import urllib.request
from pathlib import Path

ORCH_URL = os.environ.get("GPU_ORCH_URL", "http://127.0.0.1:8090")
OCR_PORT = int(os.environ.get("OCR_PORT", "8083"))
OCR_PROMPT = "document parsing."   # DeepSeek-OCR reference prompt; "Free OCR." degrades
TRANSCRIBE_TMO = 300

# The model outputs grounded layout lines like:
#   text [68, 323, 484, 420]Some words here
#   header [71, 175, 195, 190]CHAPTER
#   image [71, 193, 195, 278]
#   table [95, 542, 451, 604]<table>...</table>
_DET = re.compile(r"^\s*(\w+)\s*\[\s*[\d.,\s-]*\]\s*(.*)$", re.DOTALL)

# The model sometimes refuses / hallucinates on low-signal pages, emitting
# self-referential boilerplate instead of a transcription. Drop those lines.
_HALLUC = (
    "no text detected", "image contains no text",
    "does not correspond to any content", "is a hallucination",
    "the correct ocr output", "no visible text", "no discernible text",
)


class OcrUnavailableError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Orchestrator HTTP helpers
# ---------------------------------------------------------------------------
def _orch_req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ORCH_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def orchestrator_available() -> bool:
    try:
        _orch_req("GET", "/status", timeout=5)
        return True
    except Exception:
        return False


def acquire_ocr(wait=True, timeout=600):
    """Ask the orchestrator to swap to the ocr profile and confirm it is up."""
    deadline = time.time() + timeout
    while True:
        try:
            st = _orch_req("POST", "/acquire", {"service": "ocr"}, timeout=timeout)
        except Exception as e:
            if time.time() > deadline:
                raise OcrUnavailableError(f"orchestrator acquire failed: {e}")
            time.sleep(2)
            continue
        if not wait:
            return st
        if st.get("services", {}).get("ocr", {}).get("alive"):
            return st
        if time.time() > deadline:
            raise OcrUnavailableError("orchestrator did not reach ocr profile in time")
        time.sleep(2)


def release_ocr():
    try:
        return _orch_req("POST", "/release", timeout=30)
    except Exception:
        return None


def refresh_ocr():
    """Bump the orchestrator idle timer so an in-flight OCR run isn't swapped out."""
    try:
        return _orch_req("POST", "/refresh", timeout=10)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------
def _b64_data_url(png):
    if isinstance(png, (bytes, bytearray)):
        raw = bytes(png)
    else:
        import fitz
        if isinstance(png, fitz.Pixmap):
            raw = png.tobytes("png")
        else:
            raw = Path(png).read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def transcribe_page(png, prompt: str = OCR_PROMPT, port: int = None) -> str:
    """Send one page image to the OCR server and return the grounded text."""
    port = port or OCR_PORT
    body = json.dumps({
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": _b64_data_url(png)}},
                {"type": "text", "text": prompt},
            ],
        }],
        "temperature": 0, "max_tokens": 4096, "seed": 0, "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions", body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TRANSCRIBE_TMO) as resp:
        r = json.loads(resp.read().decode())
    return r["choices"][0]["message"]["content"] or ""


# ---------------------------------------------------------------------------
# Grounded layout -> plain text
# ---------------------------------------------------------------------------
def grounded_to_text(raw: str) -> str:
    """Convert Unlimited-OCR grounded output into plain reading-order text.

    Keeps text/header/title/table/caption content, drops empty image markers,
    strips layout tags and bboxes, converts HTML tables to tab-delimited rows.
    """
    out_lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if any(p in low for p in _HALLUC):
            continue
        m = _DET.match(line)
        if m:
            kind, content = m.group(1), m.group(2).strip()
            if kind == "image":
                continue
            if kind == "page_number":
                continue
            content = _html_table_to_text(content)
            if content.strip():
                out_lines.append(content.strip())
        else:
            out_lines.append(line.strip())
    return "\n".join(out_lines)


def _html_table_to_text(s: str) -> str:
    """Turn <table><tr><td>a</td><td>b</td></tr>...</table> into rows."""
    if "<table" not in s:
        return s
    rows = re.findall(r"<tr>(.*?)</tr>", s, re.DOTALL)
    if not rows:
        return re.sub(r"<[^>]+>", " ", s).strip()
    out = []
    for r in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)
        clean = [re.sub(r"<[^>]+>", " ", c).strip() for c in cells]
        out.append("\t".join(clean))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Whole-PDF transcription (context-manager friendly)
# ---------------------------------------------------------------------------
class OcrSession:
    """Acquire the OCR profile once, transcribe many pages, release on exit."""

    def __init__(self, prompt: str = OCR_PROMPT, port: int = None):
        self.prompt = prompt
        self.port = port or OCR_PORT
        self._acquired = False

    def __enter__(self):
        acquire_ocr()
        self._acquired = True
        return self

    def __exit__(self, *exc):
        if self._acquired:
            release_ocr()
        return False

    def transcribe(self, png) -> str:
        refresh_ocr()          # keep the swap alive across a long run
        return transcribe_page(png, prompt=self.prompt, port=self.port)


def transcribe_pdf(pdf_path: str, dpi: int = 200, prompt: str = OCR_PROMPT) -> str:
    """Render every page of a PDF and OCR it under one orchestrator swap.

    Returns the cleaned, grounded text. Raises on failure so callers can fall
    back to the pypdf path.
    """
    import fitz

    if not orchestrator_available():
        raise OcrUnavailableError("orchestrator not reachable")

    with OcrSession(prompt=prompt) as ocr:
        doc = fitz.open(pdf_path)
        try:
            pages = [ocr.transcribe(doc[i].get_pixmap(dpi=dpi)) for i in range(len(doc))]
        finally:
            doc.close()

    return "\n\n".join(grounded_to_text(p) for p in pages if p.strip())


if __name__ == "__main__":
    import sys
    print("orchestrator:", "up" if orchestrator_available() else "DOWN")
    if len(sys.argv) > 1:
        t = transcribe_pdf(sys.argv[1])
        print(t[:2000])
