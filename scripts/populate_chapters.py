"""Batch-populate chapter_number and chapter_name from ToC extractor."""
import os, sys

# Ensure we can import from webapp/backend/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEBAPP_DIR = os.path.join(SCRIPT_DIR, '..', 'webapp')
if WEBAPP_DIR not in sys.path:
    sys.path.insert(0, WEBAPP_DIR)
if os.path.join(WEBAPP_DIR, 'backend') not in sys.path:
    sys.path.insert(0, os.path.join(WEBAPP_DIR, 'backend'))

import db
from toc_extractor import extract_chapter

conn = db.get_conn()
rows = conn.execute('SELECT id, path FROM pdfs ORDER BY id').fetchall()

updated = 0
for r in rows:
    info = extract_chapter(r['path'])
    if info:
        conn.execute('UPDATE pdfs SET chapter_number=?, chapter_name=? WHERE id=?',
                     (info['chapter_number'], info['chapter_name'], r['id']))
        updated += 1
    if updated % 50 == 0:
        conn.commit()
        print(f'  {updated}/{len(rows)}...')

conn.commit()
conn.close()
print(f'Done. Updated {updated}/{len(rows)} records with chapter info')