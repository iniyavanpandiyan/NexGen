"""Catalogue audit: verify DB records against actual PDFs on disk."""
import os
import sys
from collections import Counter
from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'webapp'))
from backend import db

conn = db.get_conn()
rows = conn.execute('SELECT id, class, subject, title, path, pages, words FROM pdfs ORDER BY id').fetchall()

issues = []
oks = []
for r in rows:
    d = dict(r)
    p = d['path']
    status = 'ok'
    detail = ''
    
    if not os.path.exists(p):
        status = 'missing'
        detail = 'File not found'
    elif os.path.getsize(p) == 0:
        status = 'corrupt'
        detail = 'Empty file (0 bytes)'
    else:
        try:
            reader = PdfReader(p)
            real_pages = len(reader.pages)
            if d['pages'] is not None and d['pages'] != real_pages:
                status = 'mismatch'
                detail = f'DB says {d["pages"]} pages, actual {real_pages}'
            else:
                oks.append(d['id'])
                continue
        except Exception as e:
            status = 'corrupt'
            detail = str(e)[:120]
    
    issues.append({
        'id': d['id'],
        'class': d['class'],
        'subject': d['subject'],
        'title': d['title'],
        'status': status,
        'detail': detail,
        'path': p
    })

total = len(rows)
print(f'TOTAL: {total} records')
print(f'OK:    {len(oks)}')
print(f'ISSUES: {len(issues)}')
print()

types = Counter(i['status'] for i in issues)
for t, c in types.most_common():
    print(f'  {t}: {c}')

print()
print('=== ISSUES ===')
for i in issues:
    prefix = i['path'][:80] if len(i['path']) > 80 else i['path']
    print(f'  [{i["status"]:>9}] id={i["id"]:>4} cls={i["class"]:>2} {i["subject"]:<15} pages={i["title"]:<40} -- {i["detail"]}')
    if len(i['path']) > 80:
        print(f'  {"":>12}{prefix}')

conn.close()