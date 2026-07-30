"""Populate pages/words columns and fix catalogue metadata."""
import os
import sys
from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'webapp'))
from backend import db

conn = db.get_conn()
rows = conn.execute('SELECT id, path, pages, words FROM pdfs').fetchall()

updated = 0
for r in rows:
    d = dict(r)
    p = d['path']
    if not os.path.exists(p):
        continue
    try:
        reader = PdfReader(p)
        real_pages = len(reader.pages)
        word_count = 0
        for pg in reader.pages:
            t = pg.extract_text() or ''
            word_count += len(t.split())
        
        conn.execute(
            'UPDATE pdfs SET pages=?, words=? WHERE id=?',
            (real_pages, word_count, d['id'])
        )
        updated += 1
    except Exception as e:
        print(f'  FAIL id={d["id"]}: {e}')
    
    if updated % 50 == 0:
        print(f'  Updated {updated}/{len(rows)}...')

conn.commit()
conn.close()
print(f'Done. Updated {updated}/{len(rows)} records with real pages/word counts')