"""Run OCR text extraction on all PDFs that don't have text yet."""
import sys, os, sqlite3, time, logging
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
from pipeline.lib.text_extractor import extract as ocr_extract

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('ocr-migration')

DB = os.path.join(ROOT, 'webapp', 'backend', 'studio.db')

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
no_txt = conn.execute(
    'SELECT id, path, title FROM pdfs WHERE id NOT IN (SELECT DISTINCT pdf_id FROM pdf_text)'
).fetchall()
conn.close()

log.info(f"Found {len(no_txt)} PDFs needing text extraction")

for i, (pid, path, title) in enumerate(no_txt, 1):
    start = time.time()
    try:
        result = ocr_extract(path, dpi=150)
        text = result.get("text", "")
        method = result.get("method", "pypdf")
        if text:
            conn = sqlite3.connect(DB)
            preview = text[:4000]
            conn.execute("UPDATE pdfs SET text_preview=? WHERE id=?", (preview, pid))
            conn.execute(
                "INSERT OR REPLACE INTO pdf_text (pdf_id, raw_text, method, pages, updated_at) "
                "VALUES (?, ?, ?, ?, strftime('%s','now'))",
                (pid, text, method, result.get("pages", 0)),
            )
            conn.commit()
            conn.close()
            elapsed = time.time() - start
            log.info(f"[{i}/{len(no_txt)}] PDF #{pid} ({title[:40]}): {len(text)} chars ({method}) in {elapsed:.1f}s")
        else:
            log.warning(f"[{i}/{len(no_txt)}] PDF #{pid} ({title[:40]}): empty text")
    except Exception as e:
        log.error(f"[{i}/{len(no_txt)}] PDF #{pid} ({title[:40]}): {e}")

log.info("OCR migration complete!")
