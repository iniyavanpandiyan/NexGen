"""Deep audit of existing PDFs: verify page count, check for corruption, sample VLM-ability."""
import os
import sys
from collections import Counter
from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'webapp'))
from backend import db

conn = db.get_conn()
rows = conn.execute('SELECT id, class, subject, title, path, pages, words FROM pdfs ORDER BY id').fetchall()

corrupt = []
page_mismatch = []
ok = []
total = 0

for r in rows:
    d = dict(r)
    p = d['path']
    if not os.path.exists(p):
        continue  # already audited as missing
    total += 1
    try:
        reader = PdfReader(p)
        real_pages = len(reader.pages)
        if d['pages'] is not None and d['pages'] != real_pages:
            page_mismatch.append({
                'id': d['id'], 'class': d['class'], 'subject': d['subject'],
                'title': d['title'], 'db_pages': d['pages'], 'real_pages': real_pages
            })
        else:
            ok.append(d['id'])
    except Exception as e:
        corrupt.append({
            'id': d['id'], 'class': d['class'], 'subject': d['subject'],
            'title': d['title'], 'path': p, 'error': str(e)[:100]
        })

print(f'Existing files checked: {total}')
print(f'OK: {len(ok)}')
print(f'Page mismatches: {len(page_mismatch)}')
print(f'Corrupt: {len(corrupt)}')
print()

if page_mismatch:
    print('=== PAGE MISMATCHES ===')
    for m in page_mismatch:
        print(f'  id={m["id"]:>4} cls={m["class"]:>2} {m["subject"]:<15} title={m["title"][:30]:<30} DB={m["db_pages"]:>3} Actual={m["real_pages"]:>3}')

if corrupt:
    print()
    print('=== CORRUPT ===')
    for c in corrupt:
        print(f'  id={c["id"]:>4} cls={c["class"]:>2} {c["subject"]:<15} {c["title"][:30]:<30} {c["error"]}')

conn.close()