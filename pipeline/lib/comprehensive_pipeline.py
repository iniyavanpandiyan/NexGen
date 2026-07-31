"""Comprehensive PDF analysis pipeline.

Combines three extraction modalities per page:
  1. Raw text extraction (via pypdf)
  2. Embedded image extraction (diagrams, figures)
  3. Gemma vision analysis (visual context)

Then synthesises all pages into a unified book-level context.

Designed for large NCERT textbooks (100-300 pages):
- Processes pages in parallel batches
- Caches per-page results incrementally
- Single final synthesis pass
"""
import os, json, base64, hashlib, time, re, threading, logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

LLAMA_HOST = os.environ.get("LLAMA_HOST", "http://127.0.0.1:8082")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("nexgen.comprehensive")

# Gemma concurrency cap (avoid hammering llama-server)
GEMMA_SEM = threading.Semaphore(2)
BATCH_SIZE = 3  # pages processed per Gemma call


def page_to_image(pdf_path: str, page_num: int, dpi: int = 100) -> Optional[str]:
    """Render a single PDF page to a PNG image file."""
    import tempfile, subprocess
    out_dir = tempfile.mkdtemp(prefix="comp-pdf-")
    stem = Path(pdf_path).stem
    out_path = os.path.join(out_dir, f"{stem}_p{page_num}.png")
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-f", str(page_num + 1), "-l", str(page_num + 1),
             "-r", str(dpi), "-singlefile", pdf_path, os.path.join(out_dir, f"{stem}_p{page_num}")],
            capture_output=True, timeout=60, check=True
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except Exception:
        pass
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        pix = page.get_pixmap(dpi=dpi)
        pix.save(out_path)
        doc.close()
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except ImportError:
        pass
    return None


def extract_page_text(pdf_path: str, page_num: int) -> str:
    """Fast pypdf text extraction for a single page."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        raw = reader.pages[page_num].extract_text() or ""
        return raw.strip()
    except Exception as e:
        return f""


def extract_embedded_images_page(pdf_path: str, page_num: int) -> list:
    """Extract embedded images from a single page."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        images = []
        for idx, img_info in enumerate(page.get_images(full=True)):
            xref = img_info[0]
            base_img = doc.extract_image(xref)
            w, h = base_img.get("width", 0), base_img.get("height", 0)
            images.append({
                "index": idx,
                "width": w,
                "height": h,
                "size_bytes": len(base_img["image"]),
            })
        doc.close()
        return images
    except Exception:
        return []


def _call_gemma_vision(image_path: str, page_text: str, page_num: int) -> dict:
    """Send a page image + its extracted text to Gemma for unified analysis."""
    import requests as req
    with GEMMA_SEM:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            f"You are analyzing PAGE {page_num + 1} of a CBSE/NCERT textbook.\n\n"
            f"Extracted text from this page:\n{page_text[:8000]}\n\n"
            "You are a PURE DATA INTERPRETER. Do not invent, paraphrase, reword, "
            "or embellish any content. Copy text and figure labels EXACTLY as "
            "printed on the page. Analyze this page holistically. "
            "Return ONLY valid JSON with these fields:\n"
            "- 'page_number': the page number (1-indexed)\n"
            "- 'summary': 2-3 sentence summary of what this page covers\n"
            "- 'chapter_context': the chapter name/title VERBATIM as printed on "
            "this page if visible (or null)\n"
            "- 'diagrams': array of {label, description} for any diagrams/figures/tables "
            "on this page (empty array if none) — label copied VERBATIM from the "
            "figure caption, description a factual 1-2 sentence reading of the diagram\n"
            "- 'key_concepts': array of key terms/concepts introduced on this page (max 5)\n"
            "- 'has_activity': boolean — whether this page has activity/experiment boxes\n"
            "- 'page_type': one of 'chapter_start', 'content', 'activity', 'summary', 'exercise', 'reference'\n"
            "If you cannot determine something, use null or empty array."
        )

        payload = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
            "max_tokens": 4096,
            "temperature": 0.1,
        }

        try:
            resp = req.post(f"{LLAMA_HOST}/v1/chat/completions", json=payload, timeout=120)
            if resp.status_code != 200:
                return {"page_number": page_num, "error": f"API {resp.status_code}"}
            content = resp.json()["choices"][0]["message"]["content"]
            try:
                parsed = json.loads(content)
                return parsed
            except json.JSONDecodeError:
                m = re.search(r'\{.*\}', content, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group())
                    except json.JSONDecodeError:
                        pass
                return {"page_number": page_num, "summary": content[:300], "diagrams": []}
        except Exception as e:
            return {"page_number": page_num, "error": str(e)}


def analyze_page_comprehensive(pdf_path: str, page_num: int) -> dict:
    """Full comprehensive analysis of a single page: text + images + vision."""
    start = time.time()

    # 1. Extract text
    text = extract_page_text(pdf_path, page_num)

    # 2. Extract embedded image metadata
    embedded_images = extract_embedded_images_page(pdf_path, page_num)

    # 3. Render page + Gemma vision analysis
    vision_result = {"page_number": page_num, "error": "vision skipped"}
    img_path = page_to_image(pdf_path, page_num, dpi=100)
    if img_path:
        try:
            vision_result = _call_gemma_vision(img_path, text, page_num)
        finally:
            try:
                os.remove(img_path)
                os.rmdir(os.path.dirname(img_path))
            except OSError:
                pass

    elapsed = time.time() - start
    vision_result["extracted_text_length"] = len(text)
    vision_result["embedded_image_count"] = len(embedded_images)
    vision_result["elapsed_s"] = round(elapsed, 1)

    return vision_result


def _synthesize_all_pages(all_pages: list, pdf_title: str, pdf_class: str, pdf_subject: str) -> dict:
    """Send the collected per-page analyses to Gemma for a unified book-level synthesis."""
    import requests as req

    page_summaries = []
    for p in all_pages:
        if "error" in p:
            continue
        page_summaries.append(
            f"Page {p.get('page_number', 0) + 1}: {p.get('summary', '')[:500]} | "
            f"Type: {p.get('page_type', 'content')} | "
            f"Diagrams: {len(p.get('diagrams', []))} | "
            f"Key concepts: {', '.join(p.get('key_concepts', [])[:5])}"
        )

    summaries_text = "\n".join(page_summaries[:200])  # cap at 200 pages

    prompt = (
        f"Textbook: Class {pdf_class}, Subject: {pdf_subject or '?'}, Title: {pdf_title or '?'}\n\n"
        f"Here are the page-by-page analyses of the entire textbook:\n{summaries_text}\n\n"
        "You are a PURE DATA INTERPRETER. Do not invent, paraphrase, reword, or "
        "embellish any information. Quote content EXACTLY as it appears in the source.\n"
        "Provide a JSON synthesis with:\n"
        "- 'chapter_name': the main chapter title EXACTLY as written on the title "
        "page of this PDF — copy it VERBATIM, letter-for-letter, in its original "
        "language. Never rephrase it (or null if multi-chapter)\n"
        "- 'chapter_number': the chapter number as printed (or null)\n"
        "- 'book_title': the full textbook title VERBATIM as printed\n"
        "- 'total_pages_analyzed': count of pages analyzed\n"
        "- 'all_diagrams': consolidated list of {page, label, description} where "
        "label and description are copied VERBATIM from the source page text\n"
        "- 'key_topics': list of major topics covered (max 15) — use the section "
        "headings from the book, do not invent topics\n"
        "- 'has_activities': boolean — does the book have activity/experiment sections\n"
        "- 'difficulty': one of 'basic', 'intermediate', 'advanced'\n"
        "Return ONLY valid JSON."
    )

    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "max_tokens": 8192,
        "temperature": 0.1,
    }

    try:
        resp = req.post(f"{LLAMA_HOST}/v1/chat/completions", json=payload, timeout=180)
        if resp.status_code != 200:
            return {"error": f"Synthesis API {resp.status_code}"}
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {"raw_synthesis": content[:500]}
    except Exception as e:
        return {"error": f"Synthesis failed: {e}"}


def analyze_pdf_comprehensive(pdf_path: str, pdf_id: int,
                              pdf_title: str = "", pdf_class: str = "", pdf_subject: str = "",
                              max_pages: int = None, callback=None) -> dict:
    """Run comprehensive analysis on all pages of a PDF.

    Processes pages in parallel with concurrency control.
    Returns dict with all per-page analyses + synthesis.
    """
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    if max_pages:
        total = min(total, max_pages)

    log.info(f"[comprehensive {pdf_id}] Analyzing {total} pages of {pdf_path}")

    all_pages = [None] * total
    page_nums = list(range(total))

    # Process in parallel with thread pool
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(analyze_page_comprehensive, pdf_path, pn): pn
            for pn in page_nums
        }
        for future in as_completed(futures):
            pn = futures[future]
            try:
                result = future.result()
                all_pages[pn] = result
                log.info(f"[comprehensive {pdf_id}] Page {pn + 1}/{total} done "
                         f"({result.get('elapsed_s', '?')}s)")
            except Exception as e:
                log.warning(f"[comprehensive {pdf_id}] Page {pn + 1} failed: {e}")
                all_pages[pn] = {"page_number": pn, "error": str(e)}
            if callback:
                callback(pdf_id, pn, total)

    # Synthesis pass
    log.info(f"[comprehensive {pdf_id}] Running synthesis on {total} pages")
    synthesis = _synthesize_all_pages(all_pages, pdf_title, pdf_class, pdf_subject)

    return {
        "pdf_id": pdf_id,
        "total_pages": total,
        "pages_analyzed": total,
        "per_page": all_pages,
        "synthesis": synthesis,
        "elapsed_total_s": round(sum(p.get("elapsed_s", 0) for p in all_pages if p), 1),
    }


def analyze_pdf_comprehensive_batched(pdf_path: str, pdf_id: int,
                                      pdf_title: str = "", pdf_class: str = "", pdf_subject: str = "",
                                      start_page: int = 0, max_pages: int = None,
                                      db_conn=None) -> dict:
    """Batched comprehensive analysis — processes pages in chunks and stores
    incremental results in the database (pdf_comprehensive table).

    This is designed for large PDFs where we may need to resume.
    """
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    if max_pages:
        total = min(total, start_page + max_pages)

    log.info(f"[comprehensive {pdf_id}] Running batched, pages {start_page}-{total}")

    all_results = []
    batch_start = start_page

    while batch_start < total:
        batch_end = min(batch_start + BATCH_SIZE, total)
        log.info(f"[comprehensive {pdf_id}] Batch pages {batch_start + 1}-{batch_end}")

        # Process batch sequentially (to limit memory)
        batch_results = []
        for pn in range(batch_start, batch_end):
            result = analyze_page_comprehensive(pdf_path, pn)
            batch_results.append(result)

            # Store incrementally
            if db_conn:
                _store_page_result(db_conn, pdf_id, result)

        all_results.extend(batch_results)
        batch_start = batch_end

    # Final synthesis
    log.info(f"[comprehensive {pdf_id}] Running synthesis")
    synthesis = _synthesize_all_pages(all_results, pdf_title, pdf_class, pdf_subject)

    return {
        "pdf_id": pdf_id,
        "total_pages": total,
        "pages_analyzed": len(all_results),
        "synthesis": synthesis,
        "per_page_summary": [
            {
                "page": r.get("page_number", i),
                "summary": (r.get("summary", "") or "")[:200],
                "page_type": r.get("page_type", "content"),
                "diagrams": len(r.get("diagrams", [])),
                "concepts": r.get("key_concepts", [])[:3],
            }
            for i, r in enumerate(all_results)
        ],
        "elapsed_total_s": round(sum(r.get("elapsed_s", 0) for r in all_results), 1),
    }


def _store_page_result(db_conn, pdf_id: int, result: dict):
    """Store a single page analysis in the database."""
    try:
        db_conn.execute(
            """INSERT OR REPLACE INTO pdf_comprehensive
               (pdf_id, page_number, result_json, updated_at)
               VALUES (?, ?, ?, strftime('%s','now'))""",
            (pdf_id, result.get("page_number", 0), json.dumps(result)),
        )
        db_conn.commit()
    except Exception:
        pass


def get_stored_context(db_conn, pdf_id: int) -> Optional[dict]:
    """Retrieve the full stored comprehensive context for a PDF."""
    rows = db_conn.execute(
        "SELECT page_number, result_json FROM pdf_comprehensive "
        "WHERE pdf_id=? ORDER BY page_number",
        (pdf_id,),
    ).fetchall()
    if not rows:
        return None
    pages = [json.loads(r["result_json"]) for r in rows]
    # Build a combined context string
    parts = []
    for p in pages:
        if "error" in p:
            continue
        parts.append(
            f"[Page {p.get('page_number', 0) + 1}] {p.get('summary', '')[:300]}"
        )
        for d in p.get("diagrams", []):
            label = d.get("label", "") or ""
            desc = d.get("description", "") or ""
            parts.append(f"  Diagram{': ' + label if label else ''}: {desc[:200]}")
    return {
        "pdf_id": pdf_id,
        "pages_analyzed": len(pages),
        "context_text": "\n".join(parts),
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1]
    pid = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    r = analyze_pdf_comprehensive_batched(path, pdf_id=pid, max_pages=6)
    print(json.dumps(r.get("synthesis", r), indent=2, ensure_ascii=False)[:2000])
