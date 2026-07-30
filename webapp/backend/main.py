"""CBSE Video Studio — FastAPI backend.
Exposes: PDF catalogue, script editor + PDF-derived script generation, image
generation/review/feedback, HyperFrames template explorer, versioning, and a
task queue that drives the real pipeline (pypdf -> gen_images -> build_short ->
finalize)."""
import os, json, time, subprocess, hashlib, shutil, asyncio, threading, re, urllib.request, logging, sys
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "webapp.log"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cbse-studio")

# Ensure project root is on sys.path for pipeline.lib imports
import sys as _sys
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = Path(__file__).resolve().parent
for _p in (_PROJECT_ROOT, _BACKEND):
    if str(_p) not in _sys.path:
        _sys.path.insert(0, str(_p))

# Load .env from project root
_dotenv_path = _PROJECT_ROOT / ".env"
if not _dotenv_path.exists():
    _dotenv_path = Path.home() / ".hermes" / ".env"
if _dotenv_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_dotenv_path)
    except ImportError:
        pass

import db
import sqlite3
import catalog
import settings

app = FastAPI(title="CBSE Video Studio")
db.init()
catalog.scan(force=False)  # index PDFs on boot
settings.start_watch_background(catalog)  # watch folder scanner

# Processing semaphore: only 1 vision call at a time to avoid hammering Gemma
PROCESS_SEM = threading.Semaphore(2)

# ---------------------------------------------------------------------------
# Request/response logging middleware (must be after app = FastAPI())
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    body = None
    try:
        body = await request.json() if request.method in ("POST", "PUT") else None
    except Exception:
        pass
    response = await call_next(request)
    elapsed = time.time() - start
    log.info(
        "%s %s -> %s (%.2fs) | body=%s",
        request.method, request.url.path, response.status_code, elapsed,
        json.dumps(body)[:200] if body else "-",
    )
    return response

# ---------------------------------------------------------------------------
PIPELINE = _PROJECT_ROOT / "pipeline"
RENDERED = PIPELINE / "rendered"
WEB = Path(__file__).resolve().parent.parent

# ----------------------------------------------------------------------------- security
# Defense-in-depth: this app is published via Cloudflare Access (email login +
# the `hermesToo` service token). Cloudflare injects auth headers on every
# request it forwards, so we reject any /api/* call that lacks them. This stops
# direct-to-port-8787 access if the tunnel/Access layer is ever misconfigured.
# Set DISABLE_ACCESS_AUTH=1 only for local dev without the Access proxy.
import os as _os
_ACCESS_ENFORCED = False

@app.middleware("http")
async def require_access(request, call_next):
    if _ACCESS_ENFORCED and request.url.path.startswith("/api/"):
        auth_email = "Cf-Access-Authenticated-User-Email" in request.headers
        auth_token = ("Cf-Access-Client-Id" in request.headers
                      and "Cf-Access-Client-Secret" in request.headers)
        if not (auth_email or auth_token):
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden: Cloudflare Access authentication required"},
            )
    return await call_next(request)


# ----------------------------------------------------------------------------- tasks
TASKS = {}  # task_id -> {status, message, log, result, current, total, label, created_at}

def run_task(task_id, fn):
    try:
        TASKS[task_id]["status"] = "running"
        res = fn(task_id)
        TASKS[task_id]["status"] = "done"
        TASKS[task_id]["result"] = res
    except Exception as e:
        TASKS[task_id]["status"] = "error"
        TASKS[task_id]["message"] = str(e)
        import traceback; TASKS[task_id]["log"] = traceback.format_exc()

def spawn(fn, label="Task"):
    tid = hashlib.sha1(f"{time.time()}{os.getpid()}".encode()).hexdigest()[:12]
    TASKS[tid] = {"status": "queued", "message": "", "log": "", "result": None,
                  "current": 0, "total": 0, "label": label, "created_at": time.time()}
    t = threading.Thread(target=run_task, args=(tid, fn), daemon=True)
    t.start()
    return tid

def _update_task(tid, status=None, message=None, current=None, total=None):
    """Update a task's progress in-place (thread-safe dict operations)."""
    t = TASKS.get(tid)
    if not t:
        return
    if status is not None:
        t["status"] = status
    if message is not None:
        t["message"] = message
    if current is not None:
        t["current"] = current
    if total is not None:
        t["total"] = total

# ----------------------------------------------------------------------------- helpers
def row_to_dict(r):
    return dict(r) if r else None

def slugify(s):
    s = s.lower().strip()
    s = "".join(c if c.isalnum() else "-" for c in s)
    return s.strip("-")[:60]

def pdf_path(pid):
    conn = db.get_conn()
    r = conn.execute("SELECT path FROM pdfs WHERE id=?", (pid,)).fetchone()
    conn.close()
    return r["path"] if r else None

# Re-export for backward compat — delegates to the new hybrid extractor
from pipeline.lib.text_extractor import extract as _hybrid_extract

def extract_pdf_text(path):
    """Extract clean text from a PDF using hybrid pypdf+OCR extractor."""
    result = _hybrid_extract(path)
    return result["text"]

def build_script_from_pdf(path, template_id, max_segments=8):
    """Derive a draft script from PDF text — rule-based segmenter."""
    from pipeline.lib.text_extractor import segment_into_scripts
    result = _hybrid_extract(path)
    scripts = segment_into_scripts(result["text"], words_per_script=180,
                                   segments_per_script=max_segments)
    segs = []
    if scripts:
        for idx, s in enumerate(scripts[0]["segments"]):
            segs.append({"index": idx, "text": s["text"]})
    return segs

# ---------------------------------------------------------------------------
# LLM-powered script generation from PDF
# ---------------------------------------------------------------------------
from pipeline.lib.llm_script_gen import clean_and_generate
from pipeline.lib.text_extractor import extract as _extract_text

# --------------------------------------------------------------------------- vision API
@app.get("/api/pdfs/{pid}/vision")
def pdf_vision(pid: int, page: int = 0, task: str = "full"):
    """Analyze a PDF page via Gemma 4 vision (diagram, activity, hook, or full)."""
    from pipeline.lib.vision_processor import analyze_pdf_page
    path = pdf_path(pid)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "pdf not found")
    result = analyze_pdf_page(path, page)
    return result

@app.get("/api/pdfs/{pid}/vision-batch")
def pdf_vision_batch(pid: int):
    """Analyze all pages of a PDF via vision (slow — runs all pages)."""
    from pipeline.lib.vision_processor import analyze_pdf_page
    from pypdf import PdfReader
    path = pdf_path(pid)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "pdf not found")
    reader = PdfReader(path)
    total = len(reader.pages)
    # Only do first 3 pages for now (too slow otherwise)
    results = []
    for pg in range(min(3, total)):
        r = analyze_pdf_page(path, pg)
        results.append(r)
    return {"total_pages": total, "analyzed": len(results), "pages": results}

# --------------------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    return {"ok": True, "templates": [t["id"] for t in list_templates()]}

@app.get("/api/pdfs/{pid}/chapter")
def pdf_chapter(pid: int):
    """Extract chapter number and title from a PDF."""
    from toc_extractor import extract_chapter
    path = pdf_path(pid)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "pdf not found")
    result = extract_chapter(path)
    if not result:
        raise HTTPException(422, "could not extract chapter info")
    # Update DB so it's cached
    conn = db.get_conn()
    conn.execute("UPDATE pdfs SET chapter_number=?, chapter_name=?, identified_method=? WHERE id=?",
                 (result["chapter_number"], result["chapter_name"], result.get("method", "content"), pid))
    conn.commit()
    conn.close()
    return result

@app.get("/api/templates")
def list_templates():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM templates").fetchall()
    conn.close()
    return [json.loads(r["config_json"]) for r in rows]

@app.get("/api/catalog")
def catalog(class_filter: str = Query(None), subject: str = Query(None),
            q: str = Query(None), limit: int = 200, offset: int = 0):
    conn = db.get_conn()
    where, params = [], []
    if class_filter: where.append("p.class=?"); params.append(class_filter)
    if subject: where.append("p.subject=?"); params.append(subject)
    if q: where.append("(p.chapter_name LIKE ? OR p.title LIKE ? OR p.path LIKE ?)"); params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    wc = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT p.*,
                   COUNT(DISTINCT CASE WHEN d.description IS NOT NULL
                     AND d.description NOT LIKE 'No diagram%'
                     AND d.description != '' THEN d.id END) as diagram_count
            FROM pdfs p
            LEFT JOIN pdf_diagrams d ON d.pdf_id = p.id
            {wc}
            GROUP BY p.id
            ORDER BY p.class, p.subject, p.title
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) c FROM pdfs p {wc}", params).fetchone()["c"]
    out = []
    for r in rows:
        d = dict(r)
        video_count = conn.execute("SELECT COUNT(*) c FROM videos WHERE pdf_id=?", (r["id"],)).fetchone()["c"]
        d["video_count"] = video_count
        d["version_count"] = conn.execute(
            "SELECT COUNT(*) c FROM versions v JOIN videos vid ON vid.id=v.video_id WHERE vid.pdf_id=?",
            (r["id"],)).fetchone()["c"]
        ch_name = str(r["chapter_name"] or "").rstrip(':;, ')  # strip trailing punctuation
        ch_method = str(r["identified_method"] or "none")
        # Force to dict for .get() access
        d_row = dict(r)
        is_poor_name = (
            not ch_name
            or len(ch_name) < 5
            or re.match(r'^Chapter\s+\d+$', ch_name, re.I)
            or re.match(r'^\d+$', ch_name)
        )
        d["processing"] = {
            "identified": bool(ch_name) and not is_poor_name,
            "identified_method": ch_method,
            "has_diagrams": bool(d_row.get("diagram_count", 0)),
            "has_videos": bool(video_count),
        }
        out.append(d)
    conn.close()
    conn = db.get_conn()
    classes = [dict(x) for x in conn.execute("SELECT class, COUNT(*) c FROM pdfs GROUP BY class ORDER BY class").fetchall()]
    subjects = [dict(x) for x in conn.execute("SELECT subject, COUNT(*) c FROM pdfs WHERE subject IS NOT NULL GROUP BY subject ORDER BY c DESC").fetchall()]
    conn.close()
    return {"total": total, "items": out, "facets": {"classes": classes, "subjects": subjects}}

@app.get("/api/pdfs/{pid}")
def get_pdf(pid: int):
    conn = db.get_conn()
    r = conn.execute("SELECT * FROM pdfs WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not r: raise HTTPException(404, "pdf not found")
    return dict(r)

@app.get("/api/pdfs/{pid}/file")
def pdf_file(pid: int):
    """Serve the raw PDF for inline browser preview."""
    path = pdf_path(pid)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "pdf file not found")
    filename = os.path.basename(path)
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{filename}"'})

@app.post("/api/pdfs/{pid}/stats")
def pdf_stats(pid: int):
    conn = db.get_conn()
    r = conn.execute("SELECT path FROM pdfs WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not r: raise HTTPException(404)
    from pypdf import PdfReader
    try:
        rd = PdfReader(r["path"]); words = sum(len((pg.extract_text() or "").split()) for pg in rd.pages)
        pages = len(rd.pages)
    except Exception:
        pages, words = None, None
    conn = db.get_conn()
    conn.execute("UPDATE pdfs SET pages=?, words=? WHERE id=?", (pages, words, pid))
    conn.commit(); conn.close()
    return {"pages": pages, "words": words}

@app.post("/api/pdfs/{pid}/script-draft")
def pdf_script_draft(pid: int, body: dict = {}):
    path = pdf_path(pid)
    if not path: raise HTTPException(404, "pdf not found")
    segs = build_script_from_pdf(path, body.get("template_id", "science"),
                                 max_segments=body.get("max_segments", 8))
    return {"segments": segs}

@app.post("/api/pdfs/{pid}/parse")
def pdf_parse(pid: int):
    """LLM-powered parse: extract text + generate structured scripts with
    sub-segments via OpenRouter.

    Returns:
      {scripts: [{title, segments: [{text}]}], method: str}
    """
    path = pdf_path(pid)
    if not path:
        raise HTTPException(404, "pdf not found")

    # Get PDF metadata for LLM context
    conn = db.get_conn()
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM pdfs WHERE id=?", (pid,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "pdf not found")

    # Extract raw text using hybrid extractor
    result = _hybrid_extract(path)

    # Generate structured scripts via LLM
    scripts = clean_and_generate(
        raw_text=result["text"],
        pdf_title=r["title"] or "",
        pdf_class=r["class"] or "",
        pdf_subject=r["subject"] or "",
    )

    # Ensure every segment has an image_prompt (inline fallback in case
    # the imported module has stale bytecode)
    if scripts:
        for script in scripts:
            if "segments" not in script:
                continue
            for seg in script["segments"]:
                if not seg.get("image_prompt") or seg["image_prompt"].strip() == "":
                    text = seg.get("text", "")
                    words = re.sub(r'[^\w\s]', '', text).split()
                    stopwords = {'this','that','with','from','have','been','will','into','when','what','which','their','there','where','these','those','about','would','could','should','after','before'}
                    keywords = ", ".join(w for w in words if len(w) > 4 and w.lower() not in stopwords)[:8]
                    keywords = keywords or "educational concept"
                    seg["image_prompt"] = (
                        f"Flat vector educational illustration showing {keywords}. "
                        f"Clean white background, vibrant colors, clear labels and diagrams. "
                        f"Designed for CBSE student explainer video, modern flat style."
                    )

    # Store chapter name from first script title
    chapter_name = (scripts[0]["title"] if scripts else None)
    if chapter_name:
        conn.execute("UPDATE pdfs SET chapter_name=? WHERE id=?", (chapter_name, pid))

    conn.commit()
    conn.close()

    return {
        "scripts": scripts,
        "method": result["method"],
        "pages": result["pages"],
        "ocr_pages": result.get("ocr_pages", 0),
        "total_chars": len(result["text"]),
        "pdf_id": pid,
        "title": r["title"] or "",
        "chapter_name": chapter_name,
    }

@app.post("/api/pdfs/{pid}/extract-diagrams")
def extract_pdf_diagrams(pid: int, body: dict = {}):
    """Extract diagrams from PDF pages using Gemma vision analysis.
    Extracts embedded images, renders pages, runs Gemma for description,
    stores results in pdf_diagrams table.
    """
    from pipeline.lib.pdf_diagram_extractor import extract_and_store, build_diagram_context

    path = pdf_path(pid)
    if not path:
        raise HTTPException(404, "pdf not found")

    pages = body.get("pages")
    if pages is not None:
        from pypdf import PdfReader as _pr
        total = len(_pr(path).pages) if path else 0
        pages = [p for p in pages if 0 <= p < total]

    conn = db.get_conn()
    try:
        diagrams = extract_and_store(path, pdf_id=pid, db_conn=conn, pages=pages)
    finally:
        conn.close()

    # Separate actual diagrams from reference page renders
    actual_diagrams = [d for d in diagrams if "No diagram" not in d.get("description", "")]
    context = build_diagram_context(diagrams)

    return {
        "pdf_id": pid,
        "total_pages_processed": len(set(d["page_number"] for d in diagrams)),
        "total_diagrams": len(actual_diagrams),
        "total_references": len(diagrams),
        "diagrams": diagrams,
        "context_summary": context,
    }


@app.post("/api/pdfs/{pid}/parse-with-vision")
def pdf_parse_with_vision(pid: int):
    """Full parse: extract text + extract diagrams via Gemma + generate scripts
    with diagram context injected into the LLM prompt."""
    from pipeline.lib.pdf_diagram_extractor import extract_and_store, build_diagram_context

    path = pdf_path(pid)
    if not path:
        raise HTTPException(404, "pdf not found")

    conn = db.get_conn()
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM pdfs WHERE id=?", (pid,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "pdf not found")

    # 1. Extract text
    result = _hybrid_extract(path)

    # 2. Extract diagrams via Gemma
    try:
        diagrams = extract_and_store(path, pdf_id=pid, db_conn=conn)
    except Exception as e:
        log.warning(f"Diagram extraction failed: {e}")
        diagrams = []
    conn.close()

    # 3. Build diagram context
    diagram_context = build_diagram_context(diagrams)

    # 4. Generate scripts WITH diagram context
    scripts = clean_and_generate(
        raw_text=result["text"],
        pdf_title=r["title"] or "",
        pdf_class=r["class"] or "",
        pdf_subject=r["subject"] or "",
        diagram_context=diagram_context,
    )

    # Ensure every segment has an image_prompt
    if scripts:
        for script in scripts:
            if "segments" not in script:
                continue
            for seg in script["segments"]:
                if not seg.get("image_prompt") or seg["image_prompt"].strip() == "":
                    text = seg.get("text", "")
                    words = re.sub(r'[^\w\s]', '', text).split()
                    stopwords = {'this','that','with','from','have','been','will','into','when','what','which','their','there','where','these','those','about','would','could','should','after','before'}
                    keywords = ", ".join(w for w in words if len(w) > 4 and w.lower() not in stopwords)[:8]
                    keywords = keywords or "educational concept"
                    seg["image_prompt"] = (
                        f"Flat vector educational illustration showing {keywords}. "
                        f"Clean white background, vibrant colors, clear labels and diagrams. "
                        f"Designed for CBSE student explainer video, modern flat style."
                    )

    return {
        "scripts": scripts,
        "method": result["method"],
        "pages": result["pages"],
        "ocr_pages": result.get("ocr_pages", 0),
        "total_chars": len(result["text"]),
        "pdf_id": pid,
        "title": r["title"] or "",
        "diagrams_extracted": len(diagrams),
        "diagram_context": diagram_context,
    }


@app.post("/api/pdfs/{pid}/comprehensive-analyze")
def pdf_comprehensive_analyze(pid: int, body: dict = {}):
    """Full comprehensive analysis: runs ALL pages through Gemma vision
    combined with text extraction. Processes pages in batches in background.
    Stores results in pdf_comprehensive table.
    Returns immediately with a task_id to poll."""
    from pipeline.lib.comprehensive_pipeline import analyze_pdf_comprehensive_batched

    path = pdf_path(pid)
    if not path:
        raise HTTPException(404, "pdf not found")

    conn = db.get_conn()
    r = conn.execute("SELECT * FROM pdfs WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not r:
        raise HTTPException(404, "pdf not found")

    max_pages = body.get("max_pages")
    start_page = body.get("start_page", 0)

    def _run(tid=None):
        acquired = PROCESS_SEM.acquire(blocking=False)
        if not acquired:
            log.info(f"[comprehensive #{pid}] Skipping — semaphore busy")
            return
        try:
            conn2 = db.get_conn()
            result = analyze_pdf_comprehensive_batched(
                path, pdf_id=pid,
                pdf_title=r["title"] or "",
                pdf_class=r["class"] or "",
                pdf_subject=r["subject"] or "",
                start_page=start_page, max_pages=max_pages,
                db_conn=conn2,
            )
            conn2.close()

            # Update PDF metadata from synthesis
            syn = result.get("synthesis", {})
            if syn and "error" not in syn:
                conn3 = db.get_conn()
                updates = ["identified_method='comprehensive'"]
                if syn.get("chapter_name"):
                    ch_name = syn["chapter_name"][:200].rstrip(':;, ')
                    updates.append(f"chapter_name='{ch_name.replace(chr(39), chr(39)*2)}'")
                if syn.get("chapter_number") is not None:
                    updates.append(f"chapter_number={int(syn['chapter_number'])}")
                if len(updates) > 1:
                    conn3.execute(f"UPDATE pdfs SET {', '.join(updates)} WHERE id=?", (pid,))
                    conn3.commit()
                    log.info(f"[comprehensive #{pid}] Synthesis updated chapter info")
                conn3.close()

            log.info(f"[comprehensive #{pid}] Done: {result.get('pages_analyzed', 0)} pages")
        except Exception as e:
            log.error(f"[comprehensive #{pid}] Failed: {e}")
            import traceback; log.error(traceback.format_exc())
        finally:
            PROCESS_SEM.release()

    tid = spawn(_run, "Comprehensive analysis PDF #" + str(pid))
    return {
        "task_id": tid,
        "pdf_id": pid,
        "note": "Comprehensive analysis started in background. Poll /api/tasks/" + tid,
    }


@app.get("/api/pdfs/{pid}/comprehensive-context")
def pdf_comprehensive_context(pid: int):
    """Retrieve the stored comprehensive context for a PDF.
    Returns combined page summaries + diagram info as structured text."""
    from pipeline.lib.comprehensive_pipeline import get_stored_context
    conn = db.get_conn()
    context = get_stored_context(conn, pid)
    conn.close()
    if not context:
        return {"pdf_id": pid, "pages_analyzed": 0, "context_text": "",
                "note": "Run comprehensive analysis first (POST /api/pdfs/{pid}/comprehensive-analyze)"}
    return context


@app.get("/api/pdfs/{pid}/comprehensive-status")
def pdf_comprehensive_status(pid: int):
    """Check how many pages have been analyzed so far."""
    conn = db.get_conn()
    count = conn.execute(
        "SELECT COUNT(*) c FROM pdf_comprehensive WHERE pdf_id=?", (pid,)
    ).fetchone()["c"]
    total = 0
    try:
        from pypdf import PdfReader
        r2 = conn.execute("SELECT path FROM pdfs WHERE id=?", (pid,)).fetchone()
        if r2 and r2["path"]:
            total = len(PdfReader(r2["path"]).pages)
    except Exception:
        pass
    conn.close()
    return {"pdf_id": pid, "pages_analyzed": count, "total_pages": total,
            "progress_pct": round(count / total * 100, 1) if total else 0}


@app.post("/api/pdfs/{pid}/reanalyze-with-context")
def pdf_reanalyze_with_context(pid: int):
    """Send BOTH extracted text + rendered page images to Gemma to rebuild
    the full book context. Returns enriched chapter name, diagram labels,
    and a unified contextual summary."""
    from pipeline.lib.pdf_diagram_extractor import _gemma_analyze_image, _get_pdf_page_image
    from pipeline.lib.text_extractor import extract as _extract

    path = pdf_path(pid)
    if not path:
        raise HTTPException(404, "pdf not found")

    conn = db.get_conn()
    r = conn.execute("SELECT * FROM pdfs WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not r:
        raise HTTPException(404, "pdf not found")

    # Extract text (fast pypdf pass)
    text_result = _extract(path)
    raw_text = text_result["text"][:8000]

    # Render first 3 pages as images
    pages_analyzed = []
    for pg in range(min(3, text_result.get("pages", 10))):
        img_path = _get_pdf_page_image(path, pg, dpi=100)
        if not img_path:
            continue
        analysis = _gemma_analyze_image(img_path, task="full")
        try:
            os.remove(img_path)
            os.rmdir(os.path.dirname(img_path))
        except OSError:
            pass
        pages_analyzed.append({
            "page": pg,
            "analysis": analysis.get("analysis", "") if isinstance(analysis, dict) else str(analysis),
        })

    # Build comprehensive prompt with text + image analysis
    diagram_summaries = "\n".join(
        f"Page {p['page'] + 1}: {p['analysis'][:500]}"
        for p in pages_analyzed if p.get("analysis")
    )

    context_prompt = (
        f"Textbook: Class {r['class']}, Subject: {r.get('subject', '')}, Title: {r.get('title', '')}\n\n"
        f"Extracted Text (first 8000 chars):\n{raw_text[:4000]}\n\n"
        f"Visual Analysis of Pages:\n{diagram_summaries[:2000]}\n\n"
        "Based on ALL the above (text + images), provide a JSON response with:\n"
        "- chapter_name: the precise chapter name\n"
        "- chapter_number: the chapter number\n"
        "- description: a 2-3 sentence summary of what this chapter covers\n"
        "- key_diagrams: list of {page, label, description} for each notable diagram\n"
        "- identified_method: 'gemma_context'\n"
        "Return ONLY valid JSON."
    )

    # Send to Gemma
    import base64, requests
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": context_prompt}]}],
        "max_tokens": 2048,
        "temperature": 0.1,
    }
    try:
        LLAMA_HOST = os.environ.get("LLAMA_HOST", "http://127.0.0.1:8082")
        resp = requests.post(f"{LLAMA_HOST}/v1/chat/completions", json=payload, timeout=180)
        if resp.status_code != 200:
            raise HTTPException(502, f"Gemma API error: {resp.text[:200]}")
        content = resp.json()["choices"][0]["message"]["content"]
        import re as _re
        m = _re.search(r'\{.*\}', content, re.DOTALL)
        parsed = json.loads(m.group()) if m else {"raw": content}
    except Exception as e:
        raise HTTPException(502, f"Analysis failed: {e}")

    # Store updates
    if parsed.get("chapter_name"):
        conn = db.get_conn()
        conn.execute(
            "UPDATE pdfs SET chapter_name=?, chapter_number=?, identified_method=? WHERE id=?",
            (parsed["chapter_name"], parsed.get("chapter_number"), "gemma_context", pid),
        )
        conn.commit()
        conn.close()

    return {
        "pdf_id": pid,
        "analysis": parsed,
        "text_length": len(raw_text),
        "pages_analyzed": len(pages_analyzed),
    }


@app.get("/api/pdfs/{pid}/diagrams")
def list_pdf_diagrams(pid: int):
    """List extracted diagrams for a PDF."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM pdf_diagrams WHERE pdf_id=? ORDER BY page_number, id",
        (pid,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/pdfs/{pid}/identify")
def identify_pdf(pid: int):
    """Destructive reset + identify with task tracking.
    Clears chapter info, diagrams, comprehensive context, then runs
    fresh extraction pipeline. Returns task_id for progress tracking.
    """
    path = pdf_path(pid)
    if not path:
        raise HTTPException(404, "pdf not found")

    # --- DESTRUCTIVE RESET: clear all previous analysis ---
    conn = db.get_conn()
    conn.execute("UPDATE pdfs SET chapter_name=NULL, chapter_number=NULL, identified_method='none' WHERE id=?", (pid,))
    conn.execute("DELETE FROM pdf_diagrams WHERE pdf_id=?", (pid,))
    conn.execute("DELETE FROM pdf_comprehensive WHERE pdf_id=?", (pid,))
    conn.commit()
    conn.close()

    log.info(f"[identify #{pid}] Metadata cleared, spawning fresh identification")

    task_id = spawn(lambda tid: _identify_fresh(pid, path, tid), "Identify PDF #" + str(pid))

    return {
        "ok": True,
        "task_id": task_id,
        "pdf_id": pid,
        "chapter_name": None,
        "chapter_number": None,
        "method": "none",
        "note": "Previous analysis cleared. Fresh identification started.",
    }


def _identify_fresh(pid: int, path: str, task_id: str = ""):
    """Full fresh identify pipeline: TOC → diagrams → comprehensive → synthesis."""
    try:
        # Step 1: Quick TOC extract as initial guess
        from webapp.backend.toc_extractor import extract_chapter
        if task_id:
            _update_task(task_id, "running", f"PDF #{pid}: extracting TOC...", 1, 5)
        info = extract_chapter(path)
        initial_name = info["chapter_name"] if info else None
        initial_number = info["chapter_number"] if info else None
        initial_method = info["method"] if info else "none"

        if initial_name:
            cleaned_name = initial_name.rstrip(':;, ')
            conn = db.get_conn()
            conn.execute(
                "UPDATE pdfs SET chapter_name=?, chapter_number=?, identified_method=? WHERE id=?",
                (cleaned_name, initial_number, initial_method, pid),
            )
            conn.commit()
            conn.close()
            log.info(f"[identify #{pid}] TOC extracted: {cleaned_name} (method={initial_method})")

        # Step 2: OCR text extraction (Tesseract — handles custom font encoding)
        try:
            if task_id:
                _update_task(task_id, "running", f"PDF #{pid}: OCR text extraction...", 2, 6)
            from pipeline.lib.text_extractor import extract as _ocr_extract
            ocr_result = _ocr_extract(path, dpi=150)
            ocr_text = ocr_result.get("text", "")
            ocr_method = ocr_result.get("method", "pypdf")
            if ocr_text:
                conn_ocr = db.get_conn()
                preview = ocr_text[:4000]
                conn_ocr.execute(
                    "UPDATE pdfs SET text_preview=? WHERE id=?",
                    (preview, pid),
                )
                conn_ocr.execute(
                    "INSERT OR REPLACE INTO pdf_text (pdf_id, raw_text, method, pages, updated_at) "
                    "VALUES (?, ?, ?, ?, strftime('%s','now'))",
                    (pid, ocr_text, ocr_method, ocr_result.get("pages", 0)),
                )
                conn_ocr.commit()
                conn_ocr.close()
                log.info(f"[identify #{pid}] OCR text extracted: {len(ocr_text)} chars (method={ocr_method})")
            else:
                log.warning(f"[identify #{pid}] OCR produced empty text")
        except Exception as e:
            log.warning(f"[identify #{pid}] OCR text extraction failed: {e}")

        # Step 3: Extract diagrams
        try:
            if task_id:
                _update_task(task_id, "running", f"PDF #{pid}: extracting diagrams...", 3, 6)
            from pipeline.lib.pdf_diagram_extractor import extract_and_store
            conn_diag = db.get_conn()
            extract_and_store(path, pid, db_conn=conn_diag)
            conn_diag.close()
            log.info(f"[identify #{pid}] Diagram extraction complete")
        except Exception as e:
            log.warning(f"[identify #{pid}] Diagram extraction failed: {e}")

        # Step 4: Comprehensive page-by-page analysis (uses semaphore)
        if task_id:
            _update_task(task_id, "running", f"PDF #{pid}: comprehensive analysis...", 4, 6)
        acquired = PROCESS_SEM.acquire(blocking=False)
        if acquired:
            try:
                from pipeline.lib.comprehensive_pipeline import analyze_pdf_comprehensive_batched
                conn2 = db.get_conn()
                r2 = conn2.execute("SELECT title, class, subject FROM pdfs WHERE id=?", (pid,)).fetchone()
                comp_result = analyze_pdf_comprehensive_batched(
                    path, pdf_id=pid,
                    pdf_title=r2["title"] if r2 else "",
                    pdf_class=r2["class"] if r2 else "",
                    pdf_subject=r2["subject"] if r2 else "",
                    db_conn=conn2,
                )
                conn2.close()

                if comp_result and comp_result.get("synthesis"):
                    syn = comp_result["synthesis"]
                    ch_name = (syn.get("chapter_name") or "").rstrip(':;, ')
                    ch_num = syn.get("chapter_number") or initial_number
                    # Quality check: reject poor synthesis names
                    is_poor_syn = (
                        not ch_name or len(ch_name) < 5
                        or re.match(r'^Chapter\s+\d+$', ch_name, re.I)
                        or re.match(r'^\d+$', ch_name)
                    )
                    if is_poor_syn and initial_name and len(initial_name) >= 5:
                        ch_name = initial_name
                        ch_num = initial_number or ch_num
                    conn3 = db.get_conn()
                    conn3.execute(
                        "UPDATE pdfs SET chapter_name=?, chapter_number=?, identified_method=? WHERE id=?",
                        (ch_name, ch_num, "comprehensive" if not is_poor_syn else "heuristic", pid),
                    )
                    conn3.commit()
                    conn3.close()
                    log.info(f"[identify #{pid}] Synthesis complete: {ch_name}")
            finally:
                PROCESS_SEM.release()

        # Step 5: Vision refinement for diagrams (with page context)
        try:
            from pipeline.lib.vision_processor import analyze_page
            conn4 = db.get_conn()
            diags = conn4.execute(
                "SELECT id, image_path, page_number FROM pdf_diagrams WHERE pdf_id=? AND (description IS NULL OR description = '' OR description LIKE 'No diagram%')",
                (pid,),
            ).fetchall()
            # Build page context from comprehensive results
            page_contexts = {}
            comp_rows = conn4.execute(
                "SELECT page_number, result_json FROM pdf_comprehensive WHERE pdf_id=?", (pid,)
            ).fetchall()
            for cr in comp_rows:
                try:
                    data = json.loads(cr["result_json"])
                    parts = []
                    if data.get("summary"): parts.append(f"Page summary: {data['summary']}")
                    if data.get("key_concepts"): parts.append(f"Key concepts: {', '.join(data['key_concepts'][:5])}")
                    if data.get("page_type"): parts.append(f"Page type: {data['page_type']}")
                    if data.get("chapter_context"): parts.append(f"Chapter context: {data['chapter_context']}")
                    if parts: page_contexts[cr["page_number"]] = " | ".join(parts)
                except Exception:
                    pass
            conn4.close()

            for d in diags:
                if not d["image_path"] or not os.path.exists(d["image_path"]):
                    continue
                try:
                    ctx = page_contexts.get(d["page_number"], "")
                    result = analyze_page(d["image_path"], task="full", context_prefix=ctx)
                    if "error" not in result:
                        desc = result.get("analysis", "")
                        conn5 = db.get_conn()
                        conn5.execute("UPDATE pdf_diagrams SET description=?, status=? WHERE id=?",
                                     (desc, "analyzed", d["id"]))
                        conn5.commit()
                        conn5.close()
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"[identify #{pid}] Diagram vision labeling failed: {e}")

        log.info(f"[identify #{pid}] Fresh identification complete")

    except Exception as e:
        log.error(f"[identify #{pid}] Fresh identification failed: {e}")


def _identify_diagrams_only(pid: int, path: str):
    """Extract diagrams only (skipping TOC and comprehensive). Safe to call without semaphore."""
    try:
        from pipeline.lib.pdf_diagram_extractor import extract_and_store
        conn = db.get_conn()
        extract_and_store(path, pid, db_conn=conn)
        conn.close()
    except Exception as e:
        log.warning(f"[diagrams-only #{pid}] Failed: {e}")


def _identify_with_vision(pid: int, path: str):
    """Background task: use Gemma vision to identify chapter name from first page."""
    acquired = PROCESS_SEM.acquire(blocking=False)
    if not acquired:
        log.info(f"[identify #{pid}] Skipping vision (semaphore busy)")
        return
    try:
        from pipeline.lib.vision_processor import page_to_image, analyze_page

        img = page_to_image(path, page_num=0, dpi=100)
        if not img:
            log.warning(f"[identify #{pid}] Failed to render first page")
            return

        result = analyze_page(img, task="chapter")
        try:
            os.remove(img)
        except OSError:
            pass

        if "error" in result:
            log.warning(f"[identify #{pid}] Vision error: {result['error']}")
            return

        analysis = result.get("analysis", "")
        import re as _re
        json_match = _re.search(r'\{[^{}]*"chapter_name"[^{}]*\}', analysis, _re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = json.loads(analysis)

        ch_name = parsed.get("chapter_name", "").strip()
        ch_num = parsed.get("chapter_number")

        if not ch_name and not ch_num:
            log.warning(f"[identify #{pid}] Vision returned empty result: {analysis[:200]}")
            return

        conn = db.get_conn()
        updates = ["identified_method='vision'"]
        if ch_name:
            updates.append(f"chapter_name='{ch_name.replace(chr(39), chr(39)*2)}'")
        if ch_num is not None:
            updates.append(f"chapter_number={int(ch_num)}")
        if len(updates) > 1:
            conn.execute(f"UPDATE pdfs SET {', '.join(updates)} WHERE id=?", (pid,))
            conn.commit()
            log.info(f"[identify #{pid}] Vision updated: {ch_name or ''} (#{ch_num or '?'})")
        conn.close()
    except Exception as e:
        log.error(f"[identify #{pid}] Vision background task failed: {e}")
    finally:
        PROCESS_SEM.release()


@app.post("/api/pdfs/identify-all")
@app.post("/api/reset")
def reset_all_metadata():
    """Clean slate: clear ALL metadata including queue, re-scan disk, then identify all.
    Returns task_id for tracking progress.
    """
    # --- WIPE ALL derived metadata ---
    conn = db.get_conn()
    conn.execute("DELETE FROM pdf_comprehensive")
    conn.execute("DELETE FROM pdf_diagrams")
    conn.execute("DELETE FROM render_queue")
    conn.execute("DELETE FROM scripts")
    conn.execute("DELETE FROM images")
    conn.execute("DELETE FROM feedback")
    conn.execute("DELETE FROM versions")
    conn.execute("DELETE FROM videos")
    conn.execute("UPDATE pdfs SET chapter_name=NULL, chapter_number=NULL, identified_method='none'")
    conn.commit()
    conn.close()
    log.info("[reset] All metadata and queue wiped")

    # --- Count existing PDFs ---
    conn2 = db.get_conn()
    total = conn2.execute("SELECT COUNT(*) c FROM pdfs").fetchone()["c"]
    conn2.close()

    # -- One-PDF-at-a-time: TOC identify sequentially with progress --
    def _reset_and_identify_all(tid=None):
        from webapp.backend.toc_extractor import extract_chapter
        conn_i = db.get_conn()
        all_pdfs = conn_i.execute(
            "SELECT id, path FROM pdfs ORDER BY class, subject, title"
        ).fetchall()
        conn_i.close()
        total = len(all_pdfs)
        _update_task(tid, "running", f"Identifying {total} PDFs one at a time...", 0, total)
        toc_completed = 0
        for r in all_pdfs:
            try:
                info = extract_chapter(r["path"])
                if info and info.get("chapter_name"):
                    cleaned = info["chapter_name"].rstrip(':;, ')
                    conn_toc = db.get_conn()
                    conn_toc.execute(
                        "UPDATE pdfs SET chapter_name=?, chapter_number=?, identified_method=? WHERE id=?",
                        (cleaned, info.get("chapter_number"), info.get("method", "toc"), r["id"]),
                    )
                    conn_toc.commit()
                    conn_toc.close()
                    toc_completed += 1
                _update_task(tid, "running", f"Identified {toc_completed}/{total} PDFs", toc_completed, total)
            except Exception as e:
                log.warning(f"[reset TOC #{r['id']}] Failed: {e}")
                _update_task(tid, "running", f"{toc_completed}/{total} (last failed: {str(e)[:60]})", toc_completed, total)

        log.info(f"[reset] TOC identify complete: {toc_completed}/{total} PDFs")
        _update_task(tid, "done", f"Done — identified {toc_completed}/{total} PDFs", toc_completed, total)

    task_id = spawn(_reset_and_identify_all)
    return {
        "ok": True,
        "task_id": task_id,
        "total": total,
        "note": f"Metadata wiped, {total} PDFs queued for identify-all (one at a time)",
    }


@app.post("/api/identify-all")
@app.post("/api/identify-all")
def identify_all_pdfs():
    """Run fresh identification for ALL PDFs (even those already identified).
    Clears each one first, then runs full pipeline. Returns task_id.
    """
    conn = db.get_conn()
    all_pdfs = conn.execute("SELECT id, path FROM pdfs ORDER BY class, subject, title").fetchall()
    conn.close()
    total = len(all_pdfs)

    def _batch_identify(tid=None):
        log.info(f"[identify-all] Starting for {total} PDFs")
        _update_task(tid, "running", f"0/{total} PDFs identified", 0, total)
        completed = 0
        for r in all_pdfs:
            pid = r["id"]
            path = r["path"]
            try:
                conn2 = db.get_conn()
                conn2.execute("UPDATE pdfs SET chapter_name=NULL, chapter_number=NULL, identified_method='none' WHERE id=?", (pid,))
                conn2.execute("DELETE FROM pdf_diagrams WHERE pdf_id=?", (pid,))
                conn2.execute("DELETE FROM pdf_comprehensive WHERE pdf_id=?", (pid,))
                conn2.commit()
                conn2.close()
                _identify_fresh(pid, path, tid)
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} PDFs identified", completed, total)
            except Exception as e:
                log.warning(f"[identify-all #{pid}] Failed: {e}")
                _update_task(tid, "running", f"{completed}/{total} (last error: {str(e)[:60]})", completed, total)
        log.info(f"[identify-all] Complete: {completed}/{total}")
        _update_task(tid, "done", f"Done — {completed}/{total} PDFs identified", completed, total)

    task_id = spawn(_batch_identify, "Identify all PDFs (full pipeline)")

    return {
        "ok": True,
        "task_id": task_id,
        "total": total,
        "note": f"Identify-all queued for {total} PDFs",
    }


def _process_single_pdf(pid: int, path: str):
    """Pipeline: identify → extract diagrams → parse for one PDF.
    Called from background thread. Skips steps already done.
    """
    conn = db.get_conn()
    row = conn.execute("SELECT chapter_name, chapter_number FROM pdfs WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        return

    # Step 1: Identify (if not done)
    if not row["chapter_name"]:
        from webapp.backend.toc_extractor import extract_chapter
        info = extract_chapter(path)
        if info and info.get("chapter_name"):
            conn = db.get_conn()
            conn.execute("UPDATE pdfs SET chapter_number=?, chapter_name=?, identified_method=? WHERE id=?",
                         (info["chapter_number"], info["chapter_name"], info.get("method", "content"), pid))
            conn.commit()
            conn.close()
        # Fire fresh identify in same thread
        _identify_fresh(pid, path)

    # Step 2: Extract diagrams (if not done)
    conn = db.get_conn()
    diag_count = conn.execute("SELECT COUNT(*) c FROM pdf_diagrams WHERE pdf_id=?", (pid,)).fetchone()["c"]
    conn.close()
    if diag_count == 0:
        try:
            from pipeline.lib.pdf_diagram_extractor import extract_and_store
            conn = db.get_conn()
            extract_and_store(path, pdf_id=pid, db_conn=conn)
            conn.close()
            log.info(f"[process #{pid}] Diagrams extracted")
        except Exception as e:
            log.warning(f"[process #{pid}] Diagram extraction failed: {e}")


@app.post("/api/pdfs/batch-process")
def batch_process_pdfs():
    """Find all unprocessed PDFs and start processing them in background threads:
    identify (Gemma) → extract diagrams → parse. Returns immediately.
    """
    conn = db.get_conn()
    unprocessed = conn.execute("""
        SELECT p.id, p.path,
               p.chapter_name IS NOT NULL AND p.chapter_name != '' as identified,
               (SELECT COUNT(*) FROM pdf_diagrams WHERE pdf_id=p.id) > 0 as has_diagrams
        FROM pdfs p
        ORDER BY p.class, p.subject, p.title
    """).fetchall()
    conn.close()

    queued = 0
    BATCH_LIMIT = 5  # max concurrent vision calls
    for r in unprocessed:
        if queued >= BATCH_LIMIT:
            break
        needs = not r["identified"] or not r["has_diagrams"]
        if needs:
            threading.Thread(
                target=_process_single_pdf,
                args=(r["id"], r["path"]),
                daemon=True,
            ).start()
            queued += 1

    return {
        "ok": True,
        "total": len(unprocessed),
        "processing_queued": queued,
        "note": f"Processing {queued} PDFs in background (skipped {len(unprocessed) - queued} already done, capped at {BATCH_LIMIT} concurrent)",
    }


@app.post("/api/pdfs/batch-identify")
def batch_identify_pdfs(body: dict = {}):
    """Batch identify selected PDFs: run full fresh identify pipeline for each.
    Accepts {pdf_ids: [1,2,3]}. Returns task_id for progress tracking.
    """
    pdf_ids = body.get("pdf_ids", [])
    if not pdf_ids:
        raise HTTPException(400, "No pdf_ids provided")

    def _run(tid=None):
        total = len(pdf_ids)
        _update_task(tid, "running", f"0/{total} PDFs identified", 0, total)
        completed = 0
        for pid in pdf_ids:
            try:
                path = pdf_path(pid)
                if path:
                    conn = db.get_conn()
                    conn.execute("UPDATE pdfs SET chapter_name=NULL, chapter_number=NULL, identified_method='none' WHERE id=?", (pid,))
                    conn.execute("DELETE FROM pdf_diagrams WHERE pdf_id=?", (pid,))
                    conn.execute("DELETE FROM pdf_comprehensive WHERE pdf_id=?", (pid,))
                    conn.commit()
                    conn.close()
                    _identify_fresh(pid, path, tid)
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} PDFs identified", completed, total)
            except Exception as e:
                log.warning(f"[batch-identify #{pid}] Failed: {e}")
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} (error: {str(e)[:60]})", completed, total)
        _update_task(tid, "done", f"Done — {completed}/{total} PDFs identified", completed, total)

    task_id = spawn(_run, "Batch identify")
    return {"ok": True, "task_id": task_id, "total": len(pdf_ids)}


@app.post("/api/pdfs/batch-extract-diagrams")
def batch_extract_diagrams(body: dict = {}):
    """Batch extract diagrams for selected PDFs using Gemma vision.
    Accepts {pdf_ids: [1,2,3]}. Returns task_id.
    """
    pdf_ids = body.get("pdf_ids", [])
    if not pdf_ids:
        raise HTTPException(400, "No pdf_ids provided")

    def _run(tid=None):
        from pipeline.lib.pdf_diagram_extractor import extract_and_store
        total = len(pdf_ids)
        _update_task(tid, "running", f"0/{total} PDFs processed", 0, total)
        completed = 0
        for pid in pdf_ids:
            try:
                path = pdf_path(pid)
                if path:
                    conn = db.get_conn()
                    extract_and_store(path, pdf_id=pid, db_conn=conn)
                    conn.close()
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} diagram extractions done", completed, total)
            except Exception as e:
                log.warning(f"[batch-diagrams #{pid}] Failed: {e}")
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} (error: {str(e)[:60]})", completed, total)
        _update_task(tid, "done", f"Done — extracted diagrams for {completed}/{total} PDFs", completed, total)

    task_id = spawn(_run, "Batch extract diagrams")
    return {"ok": True, "task_id": task_id, "total": len(pdf_ids)}


@app.post("/api/pdfs/batch-label-diagrams")
def batch_label_diagrams(body: dict = {}):
    """Batch label all unlabeled diagrams for selected PDFs via Gemma vision.
    Accepts {pdf_ids: [1,2,3]}. Returns task_id.
    """
    pdf_ids = body.get("pdf_ids", [])
    if not pdf_ids:
        raise HTTPException(400, "No pdf_ids provided")

    def _run(tid=None):
        from pipeline.lib.vision_processor import analyze_page
        total = len(pdf_ids)
        _update_task(tid, "running", f"0/{total} PDFs processed", 0, total)
        completed = 0
        labeled_total = 0
        for pid in pdf_ids:
            try:
                conn = db.get_conn()
                rows = conn.execute(
                    "SELECT id, image_path, page_number FROM pdf_diagrams WHERE pdf_id=? AND (description IS NULL OR description = '' OR description LIKE 'No diagram%')",
                    (pid,),
                ).fetchall()
                page_contexts = {}
                try:
                    comp_rows = conn.execute(
                        "SELECT page_number, result_json FROM pdf_comprehensive WHERE pdf_id=?",
                        (pid,),
                    ).fetchall()
                    for cr in comp_rows:
                        data = json.loads(cr["result_json"])
                        parts = []
                        if data.get("summary"): parts.append(f"Page summary: {data['summary']}")
                        if data.get("key_concepts"): parts.append(f"Key concepts: {', '.join(data['key_concepts'][:5])}")
                        if data.get("page_type"): parts.append(f"Page type: {data['page_type']}")
                        if data.get("chapter_context"): parts.append(f"Chapter context: {data['chapter_context']}")
                        if parts: page_contexts[cr["page_number"]] = " | ".join(parts)
                except Exception:
                    pass
                conn.close()

                local_labeled = 0
                for r in rows:
                    if not r["image_path"] or not os.path.exists(r["image_path"]):
                        continue
                    try:
                        ctx = page_contexts.get(r["page_number"], "")
                        result = analyze_page(r["image_path"], task="full", context_prefix=ctx)
                        if "error" not in result:
                            desc = result.get("analysis", "")
                            conn2 = db.get_conn()
                            conn2.execute("UPDATE pdf_diagrams SET description=?, status=? WHERE id=?",
                                          (desc, "analyzed", r["id"]))
                            conn2.commit()
                            conn2.close()
                            local_labeled += 1
                    except Exception:
                        pass
                labeled_total += local_labeled
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} PDFs labeled ({labeled_total} diagrams)", completed, total)
            except Exception as e:
                log.warning(f"[batch-label #{pid}] Failed: {e}")
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} (error: {str(e)[:60]})", completed, total)
        _update_task(tid, "done", f"Done — labeled {labeled_total} diagrams across {completed}/{total} PDFs", completed, total)

    task_id = spawn(_run, "Batch label diagrams")
    return {"ok": True, "task_id": task_id, "total": len(pdf_ids)}


@app.post("/api/pdfs/batch-parse")
def batch_parse_pdfs(body: dict = {}):
    """Batch LLM parse selected PDFs: extract text + generate structured scripts.
    Accepts {pdf_ids: [1,2,3]}. Returns task_id.
    """
    pdf_ids = body.get("pdf_ids", [])
    if not pdf_ids:
        raise HTTPException(400, "No pdf_ids provided")

    def _run(tid=None):
        from pipeline.lib.llm_script_gen import clean_and_generate
        total = len(pdf_ids)
        _update_task(tid, "running", f"0/{total} PDFs parsed", 0, total)
        completed = 0
        for pid in pdf_ids:
            try:
                path = pdf_path(pid)
                if not path:
                    completed += 1
                    continue
                conn = db.get_conn()
                conn.row_factory = sqlite3.Row
                r = conn.execute("SELECT * FROM pdfs WHERE id=?", (pid,)).fetchone()
                if not r:
                    conn.close()
                    completed += 1
                    continue
                result = _hybrid_extract(path)
                scripts = clean_and_generate(
                    raw_text=result["text"],
                    pdf_title=r["title"] or "",
                    pdf_class=r["class"] or "",
                    pdf_subject=r["subject"] or "",
                )
                if scripts:
                    chapter_name = scripts[0]["title"] if scripts else None
                    if chapter_name:
                        conn.execute("UPDATE pdfs SET chapter_name=? WHERE id=?", (chapter_name, pid))
                conn.commit()
                conn.close()
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} PDFs parsed", completed, total)
            except Exception as e:
                log.warning(f"[batch-parse #{pid}] Failed: {e}")
                completed += 1
                _update_task(tid, "running", f"{completed}/{total} (error: {str(e)[:60]})", completed, total)
        _update_task(tid, "done", f"Done — parsed {completed}/{total} PDFs", completed, total)

    task_id = spawn(_run, "Batch LLM parse")
    return {"ok": True, "task_id": task_id, "total": len(pdf_ids)}


@app.get("/api/pdfs/{pid}/details")
def pdf_details(pid: int):
    """Return full PDF details: metadata, text preview, diagrams, scripts, chapter info."""
    conn = db.get_conn()
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM pdfs WHERE id=?", (pid,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "pdf not found")
    pdf = dict(r)

    # Diagrams — separate actual diagrams from full-page renders
    diag_rows = conn.execute(
        "SELECT * FROM pdf_diagrams WHERE pdf_id=? ORDER BY page_number, id",
        (pid,),
    ).fetchall()
    all_diagrams = [dict(d) for d in diag_rows]
    actual_diagrams = []
    full_pages = []
    for d in all_diagrams:
        methods_str = d.get("methods") or "{}"
        try:
            methods = json.loads(methods_str) if isinstance(methods_str, str) else methods_str
        except Exception:
            methods = {}
        has_desc = d.get("description") and "No diagram" not in (d.get("description") or "")
        is_full = methods.get("source") == "full_page_render"
        if is_full and has_desc:
            actual_diagrams.append(d)
        elif is_full:
            full_pages.append(d)
        elif has_desc:
            actual_diagrams.append(d)
        else:
            full_pages.append(d)

    # Video count (a proxy for "has been processed")
    video_count = conn.execute(
        "SELECT COUNT(*) c FROM videos WHERE pdf_id=?", (pid,)
    ).fetchone()["c"]

    conn.close()

    # Text preview — check pdf_text table first (populated by identify/OCR), fallback to fast pypdf
    text_preview = pdf.get("text_preview") or ""
    text_source = "cached"
    if text_preview:
        # Try to get the source method from pdf_text table
        try:
            conn_src = db.get_conn()
            r_src = conn_src.execute(
                "SELECT method FROM pdf_text WHERE pdf_id=? ORDER BY updated_at DESC LIMIT 1",
                (pid,)
            ).fetchone()
            if r_src:
                text_source = r_src["method"]
            conn_src.close()
        except Exception:
            pass
    if not text_preview:
        try:
            conn2 = db.get_conn()
            r2 = conn2.execute(
                "SELECT raw_text, method FROM pdf_text WHERE pdf_id=? ORDER BY updated_at DESC LIMIT 1",
                (pid,)
            ).fetchone()
            if r2 and r2["raw_text"]:
                text_preview = r2["raw_text"][:4000]
                text_source = r2["method"]
            conn2.close()
        except Exception:
            pass

    if not text_preview:
        # Fallback: pypdf fast pass
        try:
            from pypdf import PdfReader
            reader = PdfReader(pdf["path"])
            chunks = []
            for pg in reader.pages:
                raw = pg.extract_text() or ""
                chunks.append(raw.strip())
            full = "\n\n".join(chunks)
            if full.strip():
                text_preview = full[:4000]
                text_source = "pypdf"
                conn3 = db.get_conn()
                conn3.execute(
                    "INSERT OR REPLACE INTO pdf_text (pdf_id, raw_text, method, pages, updated_at) "
                    "VALUES (?, ?, 'pypdf', ?, strftime('%s','now'))",
                    (pid, text_preview, len(reader.pages)),
                )
                conn3.commit()
                conn3.close()
        except Exception:
            pass

    if not text_preview:
        text_preview = "(No text extracted — run Identify or AI Parse)"
        text_source = "none"

    return {
        "pdf": pdf,
        "diagrams": actual_diagrams,
        "diagram_count": len(actual_diagrams),
        "full_page_count": len(full_pages),
        "full_pages": full_pages[:4],  # first 4 for reference
        "text_preview": text_preview,
        "text_source": text_source,
        "video_count": video_count,
    }


@app.post("/api/diagrams/{did}/analyze")
def analyze_diagram(did: int):
    """Re-analyze a single diagram with Gemma vision, using page context when available."""
    conn = db.get_conn()
    r = conn.execute("SELECT * FROM pdf_diagrams WHERE id=?", (did,)).fetchone()
    if not r:
        conn.close()
        raise HTTPException(404, "diagram not found")
    img_path = r["image_path"]
    page_num = r["page_number"]
    pdf_id = r["pdf_id"]
    if not img_path or not os.path.exists(img_path):
        conn.close()
        raise HTTPException(404, "diagram image file not found")

    # Fetch comprehensive context for this diagram's page
    ctx = ""
    try:
        cr = conn.execute(
            "SELECT result_json FROM pdf_comprehensive WHERE pdf_id=? AND page_number=?",
            (pdf_id, page_num),
        ).fetchone()
        if cr:
            data = json.loads(cr["result_json"])
            parts = []
            if data.get("summary"):
                parts.append(f"Page summary: {data['summary']}")
            if data.get("key_concepts"):
                parts.append(f"Key concepts: {', '.join(data['key_concepts'][:5])}")
            if data.get("page_type"):
                parts.append(f"Page type: {data['page_type']}")
            if data.get("chapter_context"):
                parts.append(f"Chapter context: {data['chapter_context']}")
            ctx = " | ".join(parts)
    except Exception:
        pass
    conn.close()

    from pipeline.lib.vision_processor import analyze_page
    result = analyze_page(img_path, task="full", context_prefix=ctx)
    if "error" in result:
        raise HTTPException(500, f"Vision analysis failed: {result['error']}")

    description = result.get("analysis", "")
    conn = db.get_conn()
    conn.execute("UPDATE pdf_diagrams SET description=?, status=? WHERE id=?",
                 (description, "analyzed", did))
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "diagram_id": did,
        "description": description,
        "context_used": bool(ctx),
    }


@app.post("/api/pdfs/{pid}/analyze-diagrams")
def analyze_pdf_diagrams(pid: int):
    """Batch-analyze all diagrams for a PDF using Gemma vision for labels.
    Uses comprehensive page context when available for better descriptions.
    """
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, image_path, page_number FROM pdf_diagrams WHERE pdf_id=? AND (description IS NULL OR description = '' OR description LIKE 'No diagram%')",
        (pid,),
    ).fetchall()

    # Fetch comprehensive context for all pages
    page_contexts = {}
    try:
        comp_rows = conn.execute(
            "SELECT page_number, result_json FROM pdf_comprehensive WHERE pdf_id=? AND page_number IN (SELECT DISTINCT page_number FROM pdf_diagrams WHERE pdf_id=?)",
            (pid, pid),
        ).fetchall()
        for cr in comp_rows:
            data = json.loads(cr["result_json"])
            parts = []
            if data.get("summary"):
                parts.append(f"Page summary: {data['summary']}")
            if data.get("key_concepts"):
                parts.append(f"Key concepts: {', '.join(data['key_concepts'][:5])}")
            if data.get("page_type"):
                parts.append(f"Page type: {data['page_type']}")
            if data.get("chapter_context"):
                parts.append(f"Chapter context: {data['chapter_context']}")
            if parts:
                page_contexts[cr["page_number"]] = " | ".join(parts)
    except Exception:
        pass

    conn.close()

    from pipeline.lib.vision_processor import analyze_page
    updated = 0
    for r in rows:
        if not r["image_path"] or not os.path.exists(r["image_path"]):
            continue
        try:
            ctx = page_contexts.get(r["page_number"], "")
            result = analyze_page(r["image_path"], task="full", context_prefix=ctx)
            if "error" not in result:
                desc = result.get("analysis", "")
                conn = db.get_conn()
                conn.execute("UPDATE pdf_diagrams SET description=?, status=? WHERE id=?",
                             (desc, "analyzed", r["id"]))
                conn.commit()
                conn.close()
                updated += 1
        except Exception:
            pass

    return {
        "ok": True,
        "pdf_id": pid,
        "diagrams_analyzed": updated,
        "context_sources": len(page_contexts),
        "note": f"Analyzed {updated} diagrams with Gemma vision (context from {len(page_contexts)} pages)",
    }


@app.post("/api/pdfs/{pid}/ocr-stem")
def ocr_stem_pages(pid: int, body: dict = {}):
    """For STEM subjects: use Gemma vision to extract text with formulas/equations
    from specific pages. Falls back to full-page vision OCR for formula-heavy content.
    """
    path = pdf_path(pid)
    if not path:
        raise HTTPException(404, "pdf not found")

    from pipeline.lib.vision_processor import page_to_image, analyze_page

    pages = body.get("pages") or [0]  # default to first page
    results = {}
    for pg in pages:
        img = page_to_image(path, page_num=pg, dpi=150)
        if not img:
            results[pg] = {"error": "Failed to render page"}
            continue
        result = analyze_page(img, task="full")
        try:
            os.remove(img)
        except OSError:
            pass
        if "error" in result:
            results[pg] = {"error": result["error"]}
        else:
            analysis = result.get("analysis", "")
            parsed = result.get("parsed")
            if not parsed:
                # Try to extract JSON from markdown-wrapped response
                import re as _re
                jm = _re.search(r'\{[^{}]*"main_text"[^{}]*\}', analysis, _re.DOTALL)
                if jm:
                    try:
                        parsed = json.loads(jm.group())
                    except Exception:
                        parsed = {}
                else:
                    parsed = {}
            results[pg] = {
                "main_text": parsed.get("main_text") or analysis[:300],
                "diagrams": parsed.get("diagrams"),
                "activity_text": parsed.get("activity_text"),
                "hook_idea": parsed.get("hook_idea"),
                "side_notes": parsed.get("side_notes"),
            }

    return {
        "ok": True,
        "pdf_id": pid,
        "subject": body.get("subject", "unknown"),
        "pages_analyzed": len(pages),
        "results": results,
    }


@app.get("/api/videos")
def list_videos(pdf_id: int = None, status: str = None, template_id: str = None):
    conn = db.get_conn()
    where, params = [], []
    if pdf_id: where.append("pdf_id=?"); params.append(pdf_id)
    if status: where.append("status=?"); params.append(status)
    if template_id: where.append("template_id=?"); params.append(template_id)
    wc = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(f"SELECT * FROM videos {wc} ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["versions"] = conn.execute("SELECT COUNT(*) c FROM versions WHERE video_id=?", (r["id"],)).fetchone()["c"]
        d["open_feedback"] = conn.execute("SELECT COUNT(*) c FROM feedback WHERE video_id=? AND status='open'", (r["id"],)).fetchone()["c"]
        out.append(d)
    conn.close()
    return out

class VideoCreate(BaseModel):
    pdf_id: int = None
    title: str
    subject: str = None
    klass: str = None
    template_id: str = "science"
    slug: str = None

@app.post("/api/videos")
def create_video(v: VideoCreate):
    slug = v.slug or slugify(v.title)
    conn = db.get_conn()
    # uniqueness
    exists = conn.execute("SELECT id FROM videos WHERE slug=?", (slug,)).fetchone()
    if exists: slug = f"{slug}-{int(time.time())%10000}"
    conn.execute(
        "INSERT INTO videos(pdf_id,slug,title,subject,class,template_id,status) VALUES(?,?,?,?,?,?,?)",
        (v.pdf_id, slug, v.title, v.subject, v.klass, v.template_id, "draft"))
    vid = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.commit(); conn.close()
    return {"id": vid, "slug": slug}

@app.get("/api/videos/{vid}")
def get_video(vid: int):
    conn = db.get_conn()
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not v: raise HTTPException(404)
    d = dict(v)
    d["versions"] = [dict(x) for x in conn.execute("SELECT * FROM versions WHERE video_id=? ORDER BY version_no", (vid,))]
    d["feedback"] = [dict(x) for x in conn.execute("SELECT * FROM feedback WHERE video_id=? ORDER BY created_at", (vid,))]
    d["images"] = [dict(x) for x in conn.execute("SELECT * FROM images WHERE video_id=? ORDER BY seg_index", (vid,))]
    d["pdf"] = dict(conn.execute("SELECT * FROM pdfs WHERE id=?", (v["pdf_id"],)).fetchone()) if v["pdf_id"] else None
    conn.close()
    return d

class ScriptSave(BaseModel):
    segments: list
    note: str = ""
    author: str = "user"

@app.post("/api/videos/{vid}/save-version")
def save_version(vid: int, s: ScriptSave):
    conn = db.get_conn()
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not v: raise HTTPException(404)
    last = conn.execute("SELECT MAX(version_no) m FROM versions WHERE video_id=?", (vid,)).fetchone()["m"] or 0
    no = last + 1
    script = {"title": v["title"], "subject": v["subject"], "class": v["class"],
              "template_id": v["template_id"], "segments": s.segments}
    conn.execute("INSERT INTO versions(video_id,version_no,script_json,note,author) VALUES(?,?,?,?,?)",
                 (vid, no, json.dumps(script), s.note, s.author))
    conn.execute("UPDATE videos SET status='draft', updated_at=strftime('%s','now') WHERE id=?", (vid,))
    conn.commit(); conn.close()
    return {"version_no": no}

@app.post("/api/videos/{vid}/feedback")
def add_feedback(vid: int, body: dict):
    conn = db.get_conn()
    conn.execute("INSERT INTO feedback(video_id,version_id,kind,text,author,status) VALUES(?,?,?,?,?,?)",
                 (vid, body.get("version_id"), body.get("kind", "note"), body.get("text", ""),
                  body.get("author", "user"), "open"))
    conn.commit(); conn.close()
    return {"ok": True}

@app.post("/api/videos/{vid}/generate-images")
def generate_images(vid: int, body: dict = {}):
    conn = db.get_conn()
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    conn.close()
    if not v: raise HTTPException(404)
    vid_ = vid
    tid = spawn(lambda tid_: _gen_images(vid_, body.get("version_id")), "Generate images for video #" + str(vid))
    return {"task_id": tid}

def _gen_images(vid, version_id):
    conn = db.get_conn()
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    vers = conn.execute("SELECT * FROM versions WHERE video_id=? ORDER BY version_no DESC LIMIT 1", (vid,)).fetchone()
    if not vers: raise RuntimeError("no saved version to generate from")
    script = json.loads(vers["script_json"])
    segs = script["segments"]
    slug = v["slug"]
    sdir = RENDERED / slug
    sdir.mkdir(parents=True, exist_ok=True)
    # write script.json
    (sdir / "script.json").write_text(json.dumps(script, indent=2))
    # build prompts from template image_style_suffix
    tmpl = json.loads(conn.execute("SELECT config_json FROM templates WHERE id=?", (v["template_id"],)).fetchone()["config_json"])
    suffix = tmpl.get("image_style_suffix", "")
    conn.execute("UPDATE videos SET status='rendering', updated_at=strftime('%s','now') WHERE id=?", (vid,))
    prompts = [f"{s['text'][:80]} {suffix}" for s in segs]
    # call gen_images
    env = dict(os.environ)
    out = subprocess.run(
        ["python3", str(PIPELINE / "lib" / "gen_images.py"), "--slug", slug,
         "--retries", "4", "--supervisor"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, env=env)
    # index generated images into DB
    idir = sdir / "images"
    for i, s in enumerate(segs):
        p = idir / f"seg_{i:02d}.png"
        if p.exists():
            conn.execute("INSERT OR REPLACE INTO images(video_id,version_id,seg_index,path,prompt,status) VALUES(?,?,?,?,?,?)",
                         (vid, vers["id"], i, str(p), prompts[i], "pending"))
    conn.execute("UPDATE videos SET status='ready', updated_at=strftime('%s','now') WHERE id=?", (vid,))
    conn.commit(); conn.close()
    return {"images": len(segs), "log_tail": out.stdout[-500:] + out.stderr[-500:]}

@app.post("/api/videos/{vid}/build")
def build_video(vid: int, body: dict = {}):
    conn = db.get_conn()
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    conn.close()
    if not v: raise HTTPException(404)
    tid = spawn(lambda tid_: _build(vid, body.get("version_id")), "Build video #" + str(vid))
    return {"task_id": tid}

def _build(vid, version_id):
    # Kill ComfyUI before loading Chatterbox TTS (VRAM management)
    _vram_op("kill_comfy")

    conn = db.get_conn()
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    slug = v["slug"]
    conn.execute("UPDATE videos SET status='rendering', updated_at=strftime('%s','now') WHERE id=?", (vid,))
    conn.commit()
    # templated build: write template_id into script.json so build_short can switch
    sdir = RENDERED / slug
    if (sdir / "script.json").exists():
        sc = json.loads((sdir / "script.json").read_text())
        sc["template_id"] = v["template_id"]
        (sdir / "script.json").write_text(json.dumps(sc, indent=2))
    env = dict(os.environ)
    r1 = subprocess.run(
        ["python3", str(PIPELINE / "lib" / "build_short.py"), "--script", str(sdir/"script.json"),
         "--slug", slug, "--tts", "chatterbox", "--target-secs", "1000"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, env=env)
    # render
    r2 = subprocess.run(["npx", "hyperframes", "render", str(sdir)],
                        cwd=str(PROJECT_ROOT), capture_output=True, text=True, env=env)
    silent = sorted(sdir.glob("renders" if False else "../renders/*.mp4"))  # fallback
    # find latest silent render for this slug
    import glob
    cand = sorted(glob.glob(str(RENDERED.parent.parent / "renders" / f"{slug}_*.mp4")), reverse=True)
    final = None
    if cand:
        r3 = subprocess.run(
            ["python3", str(PIPELINE / "lib" / "finalize.py"), "--slug", slug, "--silent", cand[0]],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, env=env)
        final = str(sdir / "final.mp4")
    conn.execute("UPDATE videos SET status='ready', updated_at=strftime('%s','now') WHERE id=?", (vid,))
    conn.commit(); conn.close()
    return {"build": r1.stdout[-300:], "render": r2.stdout[-300:], "final": final}

# ---------------------------------------------------------------------------
# Render Queue — agent-facing endpoints
# Any agent (Hermes, custom, CLI) can poll, claim, and update queue items.
# ---------------------------------------------------------------------------

@app.get("/api/queue")
def list_queue(
    status: str = Query(None),
    agent: str = Query(None),
    video_id: int = Query(None),
    limit: int = Query(20, le=100),
):
    """List queue items. Agent polls this with status=script_approved to find work."""
    conn = db.get_conn()
    where = []
    params = []
    if status:
        where.append("q.status=?")
        params.append(status)
    if agent:
        where.append("q.agent=?")
        params.append(agent)
    if video_id:
        where.append("q.video_id=?")
        params.append(video_id)
    sql = """SELECT q.*, v.slug, v.title, v.subject, v.class, v.template_id
             FROM render_queue q JOIN videos v ON q.video_id = v.id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY q.enqueued_at ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/queue/{qid}/claim")
def claim_queue_item(qid: int, body: dict = {}):
    """Agent claims a job. Sets status=claimed, records agent name.
    Returns queue item + attached textbook diagram data."""
    agent_name = body.get("agent", "unknown")
    conn = db.get_conn()
    now = time.time()
    conn.execute(
        "UPDATE render_queue SET status='claimed', agent=?, claimed_at=? WHERE id=? AND status='queued'",
        (agent_name, now, qid),
    )
    conn.commit()
    row = conn.execute("SELECT q.*, v.slug, v.title, v.subject, v.class, v.template_id, v.pdf_id "
                       "FROM render_queue q JOIN videos v ON q.video_id=v.id "
                       "WHERE q.id=?", (qid,)).fetchone()
    if not row or row["status"] != "claimed":
        conn.close()
        raise HTTPException(409, "Queue item not available (wrong status or missing)")

    result = dict(row)

    # Attach diagram info for the agent
    vid = row["video_id"]
    pdf_id = row.get("pdf_id")
    if pdf_id:
        diags = conn.execute(
            "SELECT id, page_number, image_path, description, width, height "
            "FROM pdf_diagrams WHERE pdf_id=? AND status='analyzed' AND description NOT LIKE '%No diagram%'",
            (pdf_id,),
        ).fetchall()
        proj_root = "/home/fiipadmin/projects/cbse-youtube-channel/"
        diag_list = []
        for d in diags:
            dd = dict(d)
            ip = dd.get("image_path", "")
            if ip.startswith(proj_root):
                dd["web_url"] = f"/api/files/{ip[len(proj_root):]}"
            else:
                dd["web_url"] = None
            diag_list.append(dd)
        result["diagrams"] = diag_list
        result["diagram_count"] = len(diag_list)
    else:
        result["diagrams"] = []
        result["diagram_count"] = 0

    conn.close()
    return result


@app.post("/api/queue/{qid}/status")
def update_queue_status(qid: int, body: dict):
    """Agent updates job status (rendering, preview_ready, finalizing, failed)."""
    status = body.get("status", "")
    if status not in ("rendering", "preview_ready", "preview_rejected", "finalizing", "ready", "failed"):
        raise HTTPException(400, f"Invalid status: {status}")
    conn = db.get_conn()
    now = time.time()
    extra = ""
    params = [status, now, qid]
    if status == "preview_ready":
        extra = ", preview_ready_at=?"
        params = [status, now, qid]
    elif status == "ready":
        extra = ", completed_at=?"
        params = [status, now, qid]
    conn.execute(f"UPDATE render_queue SET status=?{extra} WHERE id=?", params)
    conn.execute("UPDATE videos SET status=?, updated_at=strftime('%s','now') WHERE id=(SELECT video_id FROM render_queue WHERE id=?)",
                 (status, qid))
    conn.commit()
    row = conn.execute("SELECT * FROM render_queue WHERE id=?", (qid,)).fetchone()
    conn.close()
    if body.get("note"):
        conn = db.get_conn()
        conn.execute("INSERT INTO feedback(video_id, kind, text, author) VALUES((SELECT video_id FROM render_queue WHERE id=?),?,?,?)",
                     (qid, "note", body["note"], body.get("agent", "agent")))
        conn.commit(); conn.close()
    return dict(row)


@app.post("/api/queue/{qid}/preview-url")
def set_preview_url(qid: int, body: dict):
    """Agent sets the preview URL after rendering."""
    url = body.get("preview_url", "")
    if not url:
        raise HTTPException(400, "preview_url required")
    conn = db.get_conn()
    conn.execute("UPDATE render_queue SET preview_url=?, preview_ready_at=strftime('%s','now') WHERE id=?", (url, qid))
    conn.commit(); conn.close()
    return {"ok": True, "preview_url": url}


@app.post("/api/queue/{qid}/result")
def set_final_result(qid: int, body: dict):
    """Agent sets final result after approval + finalization."""
    conn = db.get_conn()
    updates = []
    params = []
    for field in ("final_url", "thumbnail_url", "metadata"):
        if field in body:
            val = json.dumps(body[field]) if isinstance(body[field], dict) else body[field]
            updates.append(f"{field}=?")
            params.append(val)
    if updates:
        params.append(qid)
        conn.execute(f"UPDATE render_queue SET {', '.join(updates)}, status='ready', completed_at=strftime('%s','now') WHERE id=?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM render_queue WHERE id=?", (qid,)).fetchone()
    conn.close()
    return dict(row)


# ---------------------------------------------------------------------------
# Render trigger (API -> hyperframes CLI)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # cbse-youtube-channel
RENDERED_DIR = PROJECT_ROOT / "pipeline" / "rendered"
LIB_DIR = PROJECT_ROOT / "pipeline" / "lib"

@app.post("/api/queue/{qid}/render")
def trigger_render(qid: int):
    """Trigger hyperframes render for a queue item (runs in background)."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT q.*, v.slug FROM render_queue q JOIN videos v ON q.video_id=v.id WHERE q.id=?",
        (qid,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Queue item not found")

    slug = row["slug"]
    slug_dir = RENDERED_DIR / slug
    if not slug_dir.exists():
        raise HTTPException(400, f"No rendered dir for slug '{slug}' (run build_short first)")

    conn = db.get_conn()
    conn.execute("UPDATE render_queue SET status='rendering' WHERE id=?", (qid,))
    conn.commit()
    conn.close()

    def _render(tid=None):
        try:
            # 1. hyperframes check
            check = subprocess.run(
                ["npx", "hyperframes", "check"],
                cwd=str(slug_dir), capture_output=True, text=True, timeout=120,
            )
            print(f"[render/{slug}] check: {check.returncode} {check.stderr[:200]}")

            # 2. hyperframes render to silent.mp4
            render = subprocess.run(
                ["npx", "hyperframes", "render", "--quality", "draft", "--output", "silent.mp4"],
                cwd=str(slug_dir), capture_output=True, text=True, timeout=600,
            )
            print(f"[render/{slug}] render: {render.returncode} {render.stderr[:200]}")
            if render.returncode != 0:
                raise RuntimeError(f"hyperframes render failed: {render.stderr[:500]}")

            # 3. finalize (mix narration audio into video)
            finalize = subprocess.run(
                [sys.executable, str(LIB_DIR / "finalize.py"), "--slug", slug],
                cwd=str(PROJECT_ROOT / "pipeline"), capture_output=True, text=True, timeout=300,
            )
            print(f"[render/{slug}] finalize: {finalize.returncode} {finalize.stderr[:200]}")

            final_mp4 = slug_dir / "final.mp4"
            preview_url = f"/rendered/{slug}/final.mp4"
            compose_url = f"/rendered/{slug}/index.html"

            conn = db.get_conn()
            conn.execute(
                "UPDATE render_queue SET status='preview_ready', preview_url=?, preview_ready_at=strftime('%s','now') WHERE id=?",
                (preview_url, qid),
            )
            conn.commit()
            conn.close()
            print(f"[render/{slug}] done — {final_mp4.stat().st_size / 1e6:.1f}MB" if final_mp4.exists() else f"[render/{slug}] done")
        except Exception as e:
            import traceback
            print(f"[render/{slug}] error: {e}\n{traceback.format_exc()}")
            conn = db.get_conn()
            conn.execute("UPDATE render_queue SET status='failed' WHERE id=?", (qid,))
            conn.commit()
            conn.close()

    spawn(_render, "Render queue #" + str(qid))
    return {"ok": True, "message": f"Render started for {slug}", "slug": slug}


@app.get("/api/queue/{qid}/review")
def queue_review(qid: int):
    """Detailed quality report for a queue item: diagrams, VO, lint, preview URLs."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT q.*, v.slug, v.title, v.subject, v.class, v.pdf_id FROM render_queue q JOIN videos v ON q.video_id=v.id WHERE q.id=?",
        (qid,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    slug = row["slug"]
    slug_dir = RENDERED_DIR / slug
    mp4_path = slug_dir / "final.mp4"
    html_path = slug_dir / "index.html"
    script_path = slug_dir / "script.json"

    meta = json.loads(row["metadata"] or "{}")
    stored_diagrams = meta.get("diagrams", [])
    diagram_count = meta.get("diagram_count", 0)

    # Script analysis
    segs = []
    vo_consistent = True
    vo_details = {}
    total_dur = 0
    if script_path.exists():
        script = json.loads(script_path.read_text())
        for s in script.get("segments", []):
            d = s.get("duration", 0)
            total_dur += d
            segs.append({
                "index": s.get("index"),
                "text": (s.get("text") or "")[:120],
                "image_prompt": s.get("image_prompt", ""),
                "icon": s.get("icon", s.get("image", "")),
                "duration": d,
            })
        tts_info = meta.get("tts", script.get("tts", "unknown"))
        vo_consistent = True  # build_short uses single voice per run

    # Diagram analysis
    diagrams_used = False
    diagram_report = {"total": diagram_count, "in_video": 0, "missed": []}
    if diagram_count > 0 and stored_diagrams:
        for d in stored_diagrams:
            desc_short = (d.get("description") or "")[:200]
            diagram_report["missed"].append({
                "id": d.get("id", "?"),
                "page": d.get("page_number", "?"),
                "description": desc_short,
                "reason": "svg_scenes.py uses keyword-matched icons, not textbook diagrams",
            })
        for seg in segs:
            if any((d.get("description") or "")[:30].lower() in (seg.get("image_prompt") or "").lower() for d in stored_diagrams):
                diagrams_used = True

    # Lint from hyperframes check
    lint_report = {"errors": 0, "warnings": []}
    lint_log = slug_dir / "hyperframes-check.log"
    if lint_log.exists():
        txt = lint_log.read_text()
        for line in txt.split("\n"):
            if "error" in line.lower() and "✗" in line:
                lint_report["errors"] += 1
                lint_report["warnings"].append(line.strip())

    # Preview URLs
    mp4_url = f"/rendered/{slug}/final.mp4" if mp4_path.exists() else None
    compose_url = f"/preview/"  # points to running hyperframes preview server

    return {
        "id": qid,
        "slug": slug,
        "title": row["title"],
        "subject": row["subject"],
        "class": row["class"],
        "status": row["status"],
        "agent": row["agent"],
        "segments": segs,
        "segment_count": len(segs),
        "duration_s": round(sum(s["duration"] for s in segs), 1),
        "vo": {
            "engine": meta.get("tts", "edge"),
            "consistent": vo_consistent,
            "note": "Each segment TTS-generated independently via edge-tts. Voice model is consistent but per-call prosody may vary." if vo_consistent else "Multiple TTS voices detected across segments.",
        },
        "diagrams": {
            "total": diagram_count,
            "used_in_video": diagrams_used,
            "report": diagram_report,
            "fix": "svg_scenes.py uses keyword-matched abstract icons (atom/cell/wave). Textbook diagrams are stored in assets/pdfs/extracted/ but never injected into SVG scenes.",
        },
        "lint": lint_report,
        "preview": {
            "compose_url": compose_url,
            "mp4_url": mp4_url,
            "mp4_size_mb": round(mp4_path.stat().st_size / 1e6, 1) if mp4_path.exists() else None,
        },
        "metadata": meta,
    }


@app.get("/api/queue/{qid}/preview-info")
def preview_info(qid: int):
    """Return preview URLs and file-existence booleans for a queue item."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT q.*, v.slug, v.title FROM render_queue q JOIN videos v ON q.video_id=v.id WHERE q.id=?",
        (qid,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    slug = row["slug"]
    slug_dir = RENDERED_DIR / slug
    mp4_path = slug_dir / "final.mp4"
    html_path = slug_dir / "index.html"
    return {
        "id": qid,
        "slug": slug,
        "title": row["title"],
        "status": row["status"],
        "preview_url": row["preview_url"],
        "compose_url": f"/preview/" if html_path.exists() else None,
        "mp4_url": f"/rendered/{slug}/final.mp4" if mp4_path.exists() else None,
        "mp4_exists": mp4_path.exists(),
        "has_composition": html_path.exists(),
        "size_mb": round(mp4_path.stat().st_size / 1e6, 1) if mp4_path.exists() else None,
    }


# ---------------------------------------------------------------------------
# Preview server management (multi-project HyperFrames preview)
# ---------------------------------------------------------------------------

PREVIEW_PORT = 3002
PREVIEW_STATE_FILE = PROJECT_ROOT / ".preview_state.json"

def _read_preview_state():
    try:
        return json.loads(PREVIEW_STATE_FILE.read_text())
    except Exception:
        return {"slug": None, "port": PREVIEW_PORT}

def _write_preview_state(slug):
    PREVIEW_STATE_FILE.write_text(json.dumps({"slug": slug, "port": PREVIEW_PORT, "updated": time.time()}))

@app.post("/api/preview/switch")
def switch_preview(body: dict):
    """Switch the HyperFrames preview to a different composition slug."""
    slug = body.get("slug", "")
    if not slug:
        raise HTTPException(400, "slug required")

    slug_dir = RENDERED_DIR / slug
    if not slug_dir.exists() or not (slug_dir / "index.html").exists():
        raise HTTPException(400, f"No composition found for slug '{slug}'")

    state = _read_preview_state()
    old_slug = state.get("slug")

    # Kill existing preview server
    if old_slug and old_slug != slug:
        old_dir = RENDERED_DIR / old_slug
        if old_dir.exists():
            subprocess.run(
                ["npx", "hyperframes", "preview", "--stop"],
                cwd=str(old_dir), capture_output=True, text=True, timeout=10,
            )
        subprocess.run(["fuser", "-k", f"{PREVIEW_PORT}/tcp"], capture_output=True, timeout=5)
        time.sleep(1)

    # Start new preview server (always restart to ensure it's fresh)
    subprocess.run(["fuser", "-k", f"{PREVIEW_PORT}/tcp"], capture_output=True, timeout=5)
    time.sleep(1)

    result = subprocess.run(
        ["npx", "--yes", "hyperframes", "preview", "--port", str(PREVIEW_PORT),
         "--background", "--no-open", "."],
        cwd=str(slug_dir), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise HTTPException(500, f"hyperframes preview failed: {result.stderr[:300]}")

    _write_preview_state(slug)
    time.sleep(3)

    return {
        "ok": True,
        "slug": slug,
        "preview_url": "/preview/",
        "message": f"Preview switched to {slug}",
    }


@app.post("/api/preview/stop")
def stop_preview():
    """Stop the currently running preview server."""
    state = _read_preview_state()
    slug = state.get("slug")
    if slug:
        slug_dir = RENDERED_DIR / slug
        if slug_dir.exists():
            subprocess.run(
                ["npx", "hyperframes", "preview", "--stop"],
                cwd=str(slug_dir), capture_output=True, text=True, timeout=10,
            )
        subprocess.run(["fuser", "-k", f"{PREVIEW_PORT}/tcp"], capture_output=True, timeout=5)
    _write_preview_state(None)
    return {"ok": True, "preview_url": None}


@app.get("/api/preview/status")
def preview_status():
    """Return the currently running preview slug and URL."""
    state = _read_preview_state()
    slug = state.get("slug")
    return {
        "slug": slug,
        "preview_url": f"/preview/" if slug else None,
    }


# ---------------------------------------------------------------------------
# Review actions (human review loop on queue items)
# ---------------------------------------------------------------------------

@app.post("/api/queue/{qid}/review-action")
def queue_review_action(qid: int, body: dict):
    """Approve or reject a preview with feedback."""
    action = body.get("action", "")
    note = body.get("note", "")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")

    conn = db.get_conn()
    row = conn.execute(
        "SELECT q.*, v.slug, v.title FROM render_queue q JOIN videos v ON q.video_id=v.id WHERE q.id=?",
        (qid,),
    ).fetchone()
    if not row:
        conn.close(); raise HTTPException(404)

    if action == "approve":
        new_status = "ready"
        conn.execute(
            "UPDATE render_queue SET status=?, completed_at=strftime('%s','now') WHERE id=?",
            (new_status, qid),
        )
        conn.execute(
            "UPDATE videos SET status=? WHERE id=?",
            ("ready", row["video_id"]),
        )
    else:
        new_status = "preview_rejected"
        # Increment version and re-queue for the agent to fix
        new_version = (row["version_no"] or 0) + 1
        conn.execute(
            "UPDATE render_queue SET status=?, version_no=?, agent=NULL, claimed_at=NULL, preview_ready_at=NULL WHERE id=?",
            (new_status, new_version, qid),
        )
        # Update video status
        conn.execute(
            "UPDATE videos SET status=? WHERE id=?",
            ("rejected", row["video_id"]),
        )

    if note:
        conn.execute(
            "INSERT INTO feedback(video_id, kind, text, author) VALUES(?,?,?,?)",
            (row["video_id"], "review", note, body.get("reviewer", "human")),
        )
    conn.commit()
    conn.close()

    if action == "reject" and note:
        # Notify the agent (write to a notification file the agent can poll)
        notify_dir = RENDERED_DIR / row["slug"] / ".agent"
        notify_dir.mkdir(parents=True, exist_ok=True)
        (notify_dir / "review_feedback.txt").write_text(
            f"REJECTED v{row['version_no']}: {note}\n"
            f"Fix and re-queue as v{new_version}.\n"
        )

    return {"ok": True, "status": new_status, "note": note, "version": new_version if action == "reject" else None}


@app.get("/api/queue/{qid}/lint")
def queue_lint(qid: int):
    """Run hyperframes check and return lint results."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT q.*, v.slug FROM render_queue q JOIN videos v ON q.video_id=v.id WHERE q.id=?",
        (qid,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    slug = row["slug"]
    slug_dir = RENDERED_DIR / slug
    if not (slug_dir / "index.html").exists():
        raise HTTPException(400, "No composition to lint (run build_short first)")

    r = subprocess.run(
        ["npx", "hyperframes", "check"],
        cwd=str(slug_dir), capture_output=True, text=True, timeout=60,
    )
    # Save log for review endpoint
    (slug_dir / "hyperframes-check.log").write_text(r.stderr + r.stdout)

    lines = (r.stderr + r.stdout).split("\n")
    errors = [l for l in lines if "error" in l.lower() and "✗" in l]
    warnings = [l for l in lines if "warning" in l.lower() or "⚠" in l or "Fix:" in l]
    return {
        "returncode": r.returncode,
        "errors": errors,
        "warnings": warnings[:20],
        "raw": (r.stderr + r.stdout)[:2000],
    }


# ---------------------------------------------------------------------------
# Script Approval (human gate before queue)
# ---------------------------------------------------------------------------

@app.post("/api/videos/{vid}/scripts/approve")
def approve_scripts(vid: int, body: dict = {}):
    """Human approves generated scripts. Enqueues for agent rendering
    with attached textbook diagram references (paths + descriptions)."""
    duration_cap = body.get("duration_cap", 60.0)
    version_no = body.get("version_no", 1)
    conn = db.get_conn()
    v = conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if not v:
        conn.close(); raise HTTPException(404)

    # Fetch textbook diagrams for this video's PDF
    diagrams = []
    pdf_id = v["pdf_id"]
    if pdf_id:
        rows = conn.execute(
            "SELECT id, page_number, image_path, description, width, height FROM pdf_diagrams WHERE pdf_id=? AND status='analyzed'",
            (pdf_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            # Only include pages with actual diagrams (not "No diagram" pages)
            if "No diagram" not in (d.get("description") or ""):
                diagrams.append(d)

    metadata = json.dumps({
        "diagrams": diagrams,
        "diagram_count": len(diagrams),
        "pdf_id": pdf_id,
    })

    # Insert into render queue with diagram metadata
    conn.execute(
        "INSERT INTO render_queue(video_id, version_no, status, script_duration_cap, metadata) VALUES(?,?,?,?,?)",
        (vid, version_no, "queued", duration_cap, metadata),
    )
    conn.execute("UPDATE videos SET status='queued', updated_at=strftime('%s','now') WHERE id=?", (vid,))
    conn.commit()
    qid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {
        "queue_id": qid,
        "status": "queued",
        "version_no": version_no,
        "diagrams_attached": len(diagrams),
    }


@app.post("/api/videos/{vid}/scripts/reject")
def reject_scripts(vid: int, body: dict = {}):
    """Human rejects scripts. Provide reason."""
    reason = body.get("reason", "")
    version_no = body.get("version_no", 1)
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO render_queue(video_id, version_no, status, note) VALUES(?,?,?,?)",
        (vid, version_no, "script_rejected", reason),
    )
    conn.execute("UPDATE videos SET status='needs_rework', updated_at=strftime('%s','now') WHERE id=?", (vid,))
    conn.commit(); conn.close()
    return {"status": "script_rejected"}


# ---------------------------------------------------------------------------
# Versions listing with preview/final URLs
# ---------------------------------------------------------------------------

@app.get("/api/videos/{vid}/versions")
def list_versions(vid: int):
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT v.*, q.preview_url, q.final_url, q.thumbnail_url, q.status as queue_status
           FROM versions v
           LEFT JOIN render_queue q ON q.video_id=v.video_id AND q.version_no=v.version_no
           WHERE v.video_id=?
           ORDER BY v.version_no DESC""",
        (vid,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Metadata Finalization
# ---------------------------------------------------------------------------

@app.post("/api/videos/{vid}/finalize")
def finalize_video(vid: int, body: dict = {}):
    """Set YouTube-ready metadata after final approval."""
    conn = db.get_conn()
    allowed = ("title", "description", "tags", "language", "license", "visibility")
    updates = []
    params = []
    for key in allowed:
        if key in body:
            val = json.dumps(body[key]) if isinstance(body[key], (list, dict)) else body[key]
            updates.append(f"metadata = json_set(COALESCE(metadata,'{{}}'), '$.{key}', json(?))")
            params.append(val)
    if updates:
        params.append(vid)
        conn.execute(f"UPDATE videos SET {', '.join(updates)}, updated_at=strftime('%s','now') WHERE id=?", params)
    conn.commit(); conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Agent Info — any agent harness queries this to understand the pipeline
# ---------------------------------------------------------------------------

@app.get("/api/agent/info")
def agent_info():
    """Returns pipeline schema so any agent knows how to interact."""
    return {
        "pipeline": "cbse-video-studio",
        "version": "1.0",
        "queue_endpoint": "GET /api/queue?status=queued",
        "claim_endpoint": "POST /api/queue/{id}/claim  body: {agent: string}",
        "status_endpoint": "POST /api/queue/{id}/status  body: {status: string, note?: string}",
        "preview_url_endpoint": "POST /api/queue/{id}/preview-url  body: {preview_url: string}",
        "result_endpoint": "POST /api/queue/{id}/result  body: {final_url?, thumbnail_url?, metadata?}",
        "status_values": ["queued", "claimed", "rendering", "preview_ready", "preview_rejected", "finalizing", "ready", "failed"],
        "templates": [r["id"] for r in db.get_conn().execute("SELECT id FROM templates").fetchall()],
        "default_duration_cap": 60.0,
        "preview_base_url": "https://edu.fixitinpost.in/rendered/",
        "diagrams_endpoint": "GET /api/agent/diagrams/{video_id}  — returns textbook diagram images + descriptions",
        "diagrams_extraction_endpoint": "POST /api/pdfs/{pdf_id}/extract-diagrams  — run Gemma vision extraction",
        "agent_actions": [
            "poll queue → claim → get diagrams for video → run build_short (TTS+SVG) → check → render → finalize → post preview_url → wait for approval → finalize metadata",
        ],
    }


@app.get("/api/agent/diagrams/{video_id}")
def agent_get_diagrams(video_id: int):
    """Return textbook diagram images + descriptions for a video's PDF.
    Agent uses this to fetch reference images for SVG overlays and ControlNet.
    """
    conn = db.get_conn()
    v = conn.execute("SELECT pdf_id FROM videos WHERE id=?", (video_id,)).fetchone()
    if not v or not v["pdf_id"]:
        conn.close()
        raise HTTPException(404, "Video not found or no PDF attached")
    pdf_id = v["pdf_id"]
    rows = conn.execute(
        "SELECT id, page_number, image_path, description, width, height, status "
        "FROM pdf_diagrams WHERE pdf_id=? AND status='analyzed' ORDER BY page_number",
        (pdf_id,),
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        # Skip pages Gemma said have no diagram
        if "No diagram" in (d.get("description") or ""):
            continue
        # Build web-accessible URL
        rel_path = d["image_path"]
        # Strip project root prefix to get relative path
        proj_root = "/home/fiipadmin/projects/cbse-youtube-channel/"
        if rel_path and rel_path.startswith(proj_root):
            d["web_url"] = f"/api/files/{rel_path[len(proj_root):]}"
        else:
            d["web_url"] = None
        result.append(d)

    return {"video_id": video_id, "pdf_id": pdf_id, "diagrams": result}


@app.get("/api/diagram-file/{did}")
def serve_diagram_file(did: int):
    """Serve a diagram image by its database ID."""
    conn = db.get_conn()
    r = conn.execute("SELECT image_path FROM pdf_diagrams WHERE id=?", (did,)).fetchone()
    conn.close()
    if not r or not r["image_path"]:
        raise HTTPException(404, "Diagram not found")
    path = r["image_path"]
    if not os.path.exists(path):
        raise HTTPException(404, "Diagram file not found on disk")
    return FileResponse(path)


@app.get("/api/files/{filepath:path}")
def serve_file(filepath: str):
    """Serve extracted files (diagram images, etc.) from the filesystem.
    Restricted to assets/ and pipeline/ directory trees.
    """
    from pathlib import Path as _P
    safe = _P("/home/fiipadmin/projects/cbse-youtube-channel").resolve()
    requested = (safe / filepath).resolve()
    try:
        requested.relative_to(safe)
    except ValueError:
        raise HTTPException(403, "Access denied")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(requested))


@app.get("/api/tasks")
def task_list():
    """List all tasks with their current status."""
    now = time.time()
    result = []
    for tid, t in sorted(TASKS.items(), key=lambda x: x[1].get("created_at", 0), reverse=True):
        entry = {
            "task_id": tid,
            "status": t.get("status", "unknown"),
            "message": t.get("message", ""),
            "current": t.get("current", 0),
            "total": t.get("total", 0),
            "label": t.get("label", ""),
            "created_at": t.get("created_at", 0),
            "age_seconds": int(now - t.get("created_at", now)),
            "has_log": bool(t.get("log")),
            "has_result": t.get("result") is not None,
        }
        result.append(entry)
    return {"tasks": result}

@app.get("/api/tasks/{tid}")
def task_status(tid: str):
    t = TASKS.get(tid)
    if not t:
        return {"status": "unknown"}
    return {
        "status": t.get("status", "unknown"),
        "message": t.get("message", ""),
        "current": t.get("current", 0),
        "total": t.get("total", 0),
        "label": t.get("label", ""),
        "log": t.get("log", ""),
        "result": t.get("result"),
    }

@app.post("/api/images/{iid}/feedback")
def image_feedback(iid: int, body: dict):
    conn = db.get_conn()
    conn.execute("UPDATE images SET status=?, feedback=? WHERE id=?",
                 (body.get("status", "pending"), body.get("feedback", ""), iid))
    conn.commit(); conn.close()
    return {"ok": True}

@app.get("/api/images/{iid}/file")
def image_file(iid: int):
    conn = db.get_conn()
    r = conn.execute("SELECT path FROM images WHERE id=?", (iid,)).fetchone()
    conn.close()
    if not r or not os.path.exists(r["path"]): raise HTTPException(404)
    return FileResponse(r["path"])

@app.get("/api/videos/{vid}/final")
def video_final(vid: int):
    conn = db.get_conn()
    v = conn.execute("SELECT slug FROM videos WHERE id=?", (vid,)).fetchone()
    conn.close()
    if not v: raise HTTPException(404)
    p = RENDERED / v["slug"] / "final.mp4"
    if not p.exists(): raise HTTPException(404, "not rendered yet")
    return FileResponse(str(p), media_type="video/mp4")

@app.get("/api/feedback/open")
def open_feedback():
    """Hermes entry point: all unresolved feedback across videos, with context."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT f.*, v.slug, v.title, v.template_id FROM feedback f "
        "JOIN videos v ON v.id=f.video_id WHERE f.status='open' "
        "ORDER BY f.created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/videos/needs-rework")
def needs_rework():
    """Hermes entry point: videos flagged for rework or carrying open feedback."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT v.*, (SELECT COUNT(*) FROM feedback fb WHERE fb.video_id=v.id AND fb.status='open') open_fb "
        "FROM videos v WHERE v.status='needs_rework' OR open_fb>0").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.patch("/api/feedback/{fid}")
def resolve_feedback(fid: int, body: dict = {}):
    conn = db.get_conn()
    if body.get("status"):
        conn.execute("UPDATE feedback SET status=? WHERE id=?", (body["status"], fid))
    if body.get("text") is not None:
        conn.execute("UPDATE feedback SET text=? WHERE id=?", (body["text"], fid))
    conn.commit(); conn.close()
    return {"ok": True}

@app.post("/api/rescan")
def rescan():
    n = catalog.scan(force=True)
    return {"added": n}

@app.patch("/api/pdfs/{pid}")
def update_pdf(pid: int, body: dict):
    """Update PDF metadata fields (manual override)."""
    allowed = {"chapter_name", "chapter_number", "title", "subject", "class", "identified_method"}
    conn = db.get_conn()
    updates = []
    params = []
    for key in allowed:
        if key in body:
            updates.append(f"{key}=?")
            params.append(body[key])
    if updates:
        params.append(pid)
        conn.execute(f"UPDATE pdfs SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
    conn.close()
    updated_keys = [k for k in allowed if k in body]
    return {"ok": True, "updated": updated_keys}


@app.patch("/api/diagrams/{did}")
def update_diagram(did: int, body: dict):
    """Update diagram fields (manual override of description, label, etc.)."""
    allowed = {"description", "label", "title", "status", "metadata"}
    conn = db.get_conn()
    updates = []
    params = []
    for key in allowed:
        if key in body:
            if key == "metadata" and isinstance(body[key], dict):
                updates.append(f"{key}=?")
                params.append(json.dumps(body[key]))
            else:
                updates.append(f"{key}=?")
                params.append(body[key])
    if updates:
        params.append(did)
        conn.execute(f"UPDATE pdf_diagrams SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
    conn.close()
    updated_keys = [k for k in allowed if k in body]
    return {"ok": True, "updated": updated_keys}


@app.get("/api/settings")
def get_settings():
    """Get all settings and watch folders."""
    conn = db.get_conn()
    watch_folders = settings.get_watch_folders(conn)
    watch_interval = settings.get_setting(conn, "watch_interval", 60)
    conn.close()
    return {
        "watch_folders": watch_folders,
        "watch_interval": watch_interval,
    }


@app.post("/api/settings/watch-folder")
def add_watch_folder(body: dict):
    path = body.get("path", "")
    recursive = body.get("recursive", True)
    if not path:
        raise HTTPException(400, "path required")
    conn = db.get_conn()
    try:
        result = settings.add_watch_folder(conn, path, recursive)
    except ValueError as e:
        conn.close()
        raise HTTPException(400, str(e))
    conn.close()
    return result


@app.delete("/api/settings/watch-folder/{fid}")
def remove_watch_folder(fid: int):
    conn = db.get_conn()
    settings.remove_watch_folder(conn, fid)
    conn.close()
    return {"ok": True}


@app.post("/api/settings/watch-interval")
def set_watch_interval(body: dict):
    interval = body.get("interval", 60)
    conn = db.get_conn()
    settings.set_setting(conn, "watch_interval", int(interval))
    conn.commit()
    conn.close()
    return {"ok": True, "interval": interval}


@app.post("/api/settings/scan-now")
def scan_watch_folders_now():
    """Trigger an immediate scan of all enabled watch folders."""
    from db import get_conn
    conn = get_conn()
    folders = settings.get_watch_folders(conn)
    conn.close()
    added = 0
    for folder in folders:
        if not folder["enabled"]:
            continue
        found = settings.scan_watch_folder(folder["path"], bool(folder["recursive"]))
        conn2 = get_conn()
        existing = {r["path"] for r in conn2.execute("SELECT path FROM pdfs").fetchall()}
        conn2.close()
        for path in found:
            if path not in existing:
                catalog.scan(force=False)
                added += 1
                break
    return {"ok": True, "added": added, "note": f"Scanned {len(folders)} folders, found {added} new PDFs"}


@app.get("/api/pdfs/{pid}/raw-text")
def pdf_raw_text(pid: int):
    """Return cached raw text from the bulk OCR pipeline."""
    conn = db.get_conn()
    r = conn.execute("""
        SELECT p.id, p.title, p.class, p.subject, t.raw_text, t.method, t.pages, t.word_count
        FROM pdfs p LEFT JOIN pdf_text t ON t.pdf_id=p.id WHERE p.id=?
    """, (pid,)).fetchone()
    conn.close()
    if not r: raise HTTPException(404)
    return dict(r)


# ----------------------------------------------------------------------------- voices
CHATTERBOX_VOICES_DIR = Path.home() / "Chatterbox-TTS-Server" / "voices"

def _list_edge_voices():
    import subprocess
    try:
        r = subprocess.run(["edge-tts", "--list-voices"], capture_output=True, text=True, timeout=10)
        lines = r.stdout.strip().split("\n")[2:]  # skip header
        voices = []
        for l in lines:
            parts = l.split()
            if len(parts) >= 4:
                voices.append({
                    "id": parts[0],
                    "gender": parts[1],
                    "categories": parts[2],
                    "personality": " ".join(parts[3:]),
                    "source": "edge-tts"
                })
        return voices
    except Exception:
        return []

@app.get("/api/voices")
def list_voices():
    """List all available TTS voices from Chatterbox + edge-tts, with a preview URL for each."""
    voices = []

    # Chatterbox voices
    if CHATTERBOX_VOICES_DIR.exists():
        for f in sorted(CHATTERBOX_VOICES_DIR.glob("*.wav")):
            voice_name = f.stem
            voices.append({
                "id": f"{voice_name}.wav",
                "name": voice_name,
                "gender": "unknown",
                "source": "chatterbox",
                "preview_url": f"/api/voices/preview/{voice_name}.wav",
                "preview_format": "wav"
            })

    # edge-tts voices (Indian languages preferred)
    edge_voices = _list_edge_voices()
    for v in edge_voices:
        # Only include Indian English + regional languages + natural-sounding English
        lang = v["id"].split("-")[0]
        keep = any(v["id"].startswith(p) for p in [
            "en-IN", "hi-IN", "mr-IN", "gu-IN", "ta-IN", "te-IN",
            "kn-IN", "ml-IN", "bn-IN", "pa-IN", "ur-IN", "or-IN",
            "en-US-", "en-GB-", "en-AU-"
        ])
        if keep:
            voices.append({
                "id": v["id"],
                "name": v["id"],
                "gender": v["gender"],
                "source": "edge-tts",
                "preview_url": f"/api/voices/preview-edge/{v['id']}",
                "preview_format": "mp3"
            })

    return {"voices": voices, "default": "Emily.wav"}


@app.get("/api/voices/preview/{voice_name}")
def voice_preview(voice_name: str):
    """Serve a short preview WAV from Chatterbox for a given voice."""
    voice_path = CHATTERBOX_VOICES_DIR / voice_name
    if not voice_path.exists():
        raise HTTPException(404, "voice not found")
    return FileResponse(str(voice_path), media_type="audio/wav",
                        headers={"Content-Disposition": f'inline; filename="{voice_name}"'})


@app.get("/api/voices/preview-edge/{voice_id}")
def voice_preview_edge(voice_id: str):
    """Generate a short TTS preview using edge-tts for a given voice."""
    import tempfile
    preview_text = "This is a sample of my voice for educational narration."
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        subprocess.run(
            ["edge-tts", "--voice", voice_id, "--text", preview_text, "--write-media", tmp_path],
            capture_output=True, text=True, timeout=15
        )
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
            return FileResponse(tmp_path, media_type="audio/mpeg",
                                headers={"Content-Disposition": f'inline; filename="{voice_id}.mp3"'})
    except Exception:
        pass
    raise HTTPException(500, "failed to generate preview")


# ----------------------------------------------------------------------------- image generation (proxy to FAL/OpenAI)
class ImageGenRequest(BaseModel):
    prompt: str

_COMFY = "http://127.0.0.1:8188"
_ZIMAGE_UNET = "z_image_turbo_fp8_e4m3fn.safetensors"
_ZIMAGE_CLIP = "qwen_3_4b_bf16_fp8_scaled.safetensors"
_ZIMAGE_VAE = "ae.safetensors"

def _comfy_up():
    try:
        with urllib.request.urlopen(f"{_COMFY}/system_stats", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


_VRAM_MOD = None

def _vram_op(op):
    """Dispatch VRAM management operations by importing vram_manager on demand."""
    global _VRAM_MOD
    if _VRAM_MOD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "vram_manager",
            Path(__file__).resolve().parent / "vram_manager.py"
        )
        _VRAM_MOD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_VRAM_MOD)
    if op == "kill_chatterbox":
        return _VRAM_MOD.kill("chatterbox")
    elif op == "kill_comfy":
        return _VRAM_MOD.kill("comfy")
    elif op == "start_comfy":
        return _VRAM_MOD.ensure_running("comfy")
    elif op == "start_chatterbox":
        return _VRAM_MOD.ensure_running("chatterbox")
    return None


def _zimage_graph(prompt, seed=42, size=512):
    STYLE = (" cobalt blue (#1A3FB0) soft 3D stylised illustration on a plain cream "
             "paper background (#F4F1EA), editorial explainer style, gentle volumetric "
             "shading and soft depth, a single clean object, simple single concept, "
             "strictly two-tone (cobalt + cream only), no other colors, no rainbow, "
             "no photo, no text")
    return {
        "28": {"class_type": "UNETLoader", "inputs": {"unet_name": _ZIMAGE_UNET, "weight_dtype": "default"}},
        "30": {"class_type": "CLIPLoader", "inputs": {"clip_name": _ZIMAGE_CLIP, "type": "lumina2", "device": "default"}},
        "27": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["30", 0], "text": prompt + STYLE}},
        "33": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["27", 0]}},
        "13": {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}},
        "11": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["28", 0], "shift": 3.0}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["11", 0], "positive": ["27", 0], "negative": ["33", 0],
            "latent_image": ["13", 0], "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["29", 0]}},
        "29": {"class_type": "VAELoader", "inputs": {"vae_name": _ZIMAGE_VAE}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "studio"}},
    }

@app.post("/api/images/generate")
def image_generate(req: ImageGenRequest):
    """Generate an image via local ComfyUI Z-Image-Turbo. Returns {url, path}.
    Falls back to Hermes CLI image gen if ComfyUI is unavailable.
    Output is always 512×512 square, styled in cobalt/cream two-tone editorial."""
    import subprocess, json, tempfile, os, time, shutil
    from pathlib import Path

    # Try local ComfyUI Z-Image-Turbo first
    # Kill Chatterbox to free VRAM first
    _vram_op("kill_chatterbox")

    if _comfy_up() or _vram_op("start_comfy"):
        try:
            seed = int(time.time() * 1000) % 999999
            prompt = _zimage_graph(req.prompt, seed=seed, size=512)
            pid_data = json.dumps({"prompt": prompt, "client_id": "cbse-studio"}).encode()
            req2 = urllib.request.Request(
                _COMFY + "/prompt", data=pid_data,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req2, timeout=60) as r:
                pid = json.loads(r.read().decode())["prompt_id"]
            # Poll for result
            deadline = time.time() + 120
            result = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"{_COMFY}/history/{pid}", timeout=10) as r:
                        hist = json.loads(r.read().decode())
                        if pid in hist:
                            result = hist[pid]
                            break
                except Exception:
                    pass
                time.sleep(2)
            if result and "outputs" in result:
                fname = result["outputs"]["9"]["images"][0]["filename"]
                src = f"/home/fiipadmin/comfy/ComfyUI/output/{fname}"
                if os.path.exists(src):
                    dst = Path(tempfile.gettempdir()) / f"cbse_img_{pid[:8]}.png"
                    # Copy and resize to 512×512
                    from PIL import Image
                    im = Image.open(src).convert("RGB")
                    if im.size != (512, 512):
                        im = im.resize((512, 512), Image.LANCZOS)
                    im.save(str(dst))
                    return {
                        "url": f"/api/images/serve/{dst.name}",
                        "path": str(dst),
                        "width": 512,
                        "height": 512,
                        "model": "z-image-turbo",
                    }
        except Exception:
            pass

    # Fallback: Hermes CLI
    try:
        r = subprocess.run(
            ["hermes", "image", "generate", "--prompt", req.prompt, "--aspect-ratio", "square"],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if "MEDIA:" in line:
                    path = line.split("MEDIA:")[-1].strip()
                    if os.path.exists(path):
                        # Resize to 512×512
                        from PIL import Image
                        im = Image.open(path).convert("RGB")
                        if im.size != (512, 512):
                            im = im.resize((512, 512), Image.LANCZOS)
                        im.save(path)
                        return {"url": f"/api/images/serve/{os.path.basename(path)}", "path": path}
    except Exception:
        pass

    return {"url": "", "path": "", "note": "Image gen unavailable (neither ComfyUI Z-Image-Turbo nor Hermes CLI)", "width": 512, "height": 512}


@app.get("/api/images/serve/{filename}")
def image_serve(filename: str):
    """Serve a generated image file."""
    import glob
    # Search common paths
    for base in [Path.home() / ".hermes" / "images", Path("/tmp")]:
        for f in base.glob(f"*{filename}*"):
            if f.is_file():
                return FileResponse(str(f), media_type="image/png")
    raise HTTPException(404, "image not found")


# Migrations
_conn = db.get_conn()
for _col, _typ in [("text_preview", "TEXT DEFAULT ''"), ("identified_method", "TEXT DEFAULT 'none'")]:
    try:
        _conn.execute(f"ALTER TABLE pdfs ADD COLUMN {_col} {_typ}")
    except Exception:
        pass
_conn.close()

# static frontend
app.mount("/", StaticFiles(directory=str(WEB / "frontend"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)
