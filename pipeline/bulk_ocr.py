"""Bulk OCR pipeline — preprocess all PDFs and cache clean raw text.

Usage:
    python3 bulk_ocr.py                    # process all PDFs
    python3 bulk_ocr.py --force            # reprocess already-cached PDFs
    python3 bulk_ocr.py --limit 50         # first 50 only (test run)
"""

import sys, os, sqlite3, json, time
from pathlib import Path

# Add backend to path for DB access
sys.path.insert(0, str(Path(__file__).resolve().parent / ".." / "webapp" / "backend"))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_ROOT = PROJECT_ROOT / "assets" / "pdfs"
DB_PATH = PROJECT_ROOT / "webapp" / "backend" / "studio.db"

# Import the hybrid extractor from pipeline
sys.path.insert(0, str(PROJECT_ROOT))
from pipeline.lib.text_extractor import extract as hybrid_extract

def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def ensure_text_table(conn):
    """Create the pdf_text cache table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pdf_text (
            pdf_id   INTEGER PRIMARY KEY REFERENCES pdfs(id) ON DELETE CASCADE,
            raw_text TEXT,
            method   TEXT,
            pages    INTEGER,
            ocr_pages INTEGER,
            word_count INTEGER,
            updated_at REAL DEFAULT (strftime('%s','now'))
        )
    """)
    conn.commit()

def process_pdf(pdf_row, force=False):
    """Extract text from a single PDF and cache it."""
    pdf_id = pdf_row["id"]
    path = pdf_row["path"]

    if not path or not os.path.exists(path):
        return {"id": pdf_id, "status": "error", "error": "file not found"}

    print(f"  [{pdf_id}] {pdf_row['class']}-{pdf_row['subject']}: {pdf_row['title']}...", end=" ", flush=True)

    try:
        result = hybrid_extract(path)
        raw_text = result.get("text", "")
        method = result.get("method", "unknown")
        pages = result.get("pages", 0)
        ocr_pages = result.get("ocr_pages", 0)
        word_count = len(raw_text.split()) if raw_text else 0

        conn = get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO pdf_text(pdf_id, raw_text, method, pages, ocr_pages, word_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, strftime('%s','now'))
        """, (pdf_id, raw_text, method, pages, ocr_pages, word_count))
        conn.commit()
        conn.close()

        print(f"✓ {method} {pages}p {word_count}w")
        return {"id": pdf_id, "status": "ok", "method": method, "pages": pages, "words": word_count}
    except Exception as e:
        print(f"✗ error: {e}")
        return {"id": pdf_id, "status": "error", "error": str(e)}

def main():
    force = "--force" in sys.argv
    limit = None
    for a in sys.argv:
        if a.startswith("--limit="):
            limit = int(a.split("=")[1])

    conn = get_conn()
    ensure_text_table(conn)

    # Get all PDFs (optionally filter to uncached)
    query = "SELECT id, class, subject, title, path FROM pdfs ORDER BY class, subject, title"
    if not force:
        query = "SELECT p.id, p.class, p.subject, p.title, p.path FROM pdfs p LEFT JOIN pdf_text t ON t.pdf_id=p.id WHERE t.pdf_id IS NULL ORDER BY p.class, p.subject, p.title"

    rows = conn.execute(query).fetchall()
    conn.close()

    total = rows
    if limit:
        total = rows[:limit]

    print(f"Processing {len(total)} PDFs{' (force mode)' if force else ''}...")
    print(f"Total PDFs in DB: {len(rows)}")

    results = {"ok": 0, "error": 0, "errors": []}
    start = time.time()

    for row in total:
        r = process_pdf(dict(row), force=force)
        if r["status"] == "ok":
            results["ok"] += 1
        else:
            results["error"] += 1
            results["errors"].append(r)

    elapsed = time.time() - start
    print(f"\nDone: {results['ok']} ok, {results['error']} errors in {elapsed:.1f}s")
    if results["errors"]:
        for e in results["errors"]:
            print(f"  Error ID {e['id']}: {e.get('error')}")

if __name__ == "__main__":
    main()
