"""Populate ONLY the words column for PDFs that already have pages."""
import os
import sys
from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'webapp'))
from backend import db

conn = db.get_conn()
rows = conn.execute('SELECT id, path FROM pdfs WHERE words IS NULL').fetchall()

updated = 0
for r in rows:
    p = r['path']
    if not os.path.exists(p):
        continue
    try:
        reader = PdfReader(p)
        word_count = 0
        for pg in reader.pages:
            t = pg.extract_text() or ''
            word_count += len(t.split())
        conn.execute('UPDATE pdfs SET words=? WHERE id=?', (word_count, r['id']))
        updated += 1
        if updated % 50 == 0:
            conn.commit()
            print(f'  Updated {updated}/{len(rows)}...')
    except Exception as e:
        print(f'  FAIL id={r["id"]}: {e}')

conn.commit()
conn.close()
print(f'Done. Updated {updated}/{len(rows)} records with word counts')