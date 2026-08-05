"""Scan the NCERT PDF library into the pdfs table."""
import os, re, sqlite3
from db import get_conn, q, commit

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PDF_ROOT = os.path.join(PROJECT_ROOT, "assets", "pdfs")

SUBJECTS = {
    "science": "Science", "maths": "Mathematics", "mathematics": "Mathematics",
    "physics": "Physics", "chemistry": "Chemistry", "biology": "Biology",
    "math": "Mathematics", "social": "Social Science", "history": "History",
    "geography": "Geography", "civics": "Civics", "political": "Political Science",
    "economics": "Economics", "accountancy": "Accountancy", "business": "Business Studies",
    "english": "English", "hindi": "Hindi", "sanskrit": "Sanskrit",
    "urdu": "Urdu",
    "health": "Health & Physical Education",
    "hpe": "Health & Physical Education",
    "vocational": "Vocational/Language",
    "language": "Vocational/Language",
    "kshitij": "Hindi (Kshitij)",
    "kritika": "Hindi (Kritika)",
    "vyakaran": "Hindi (Vyakaran)",
    "shemushi": "Sanskrit (Shemushi)",
    "jaan": "Urdu (Jaan Pehchan)",
    "pehchan": "Urdu (Jaan Pehchan)",
    "gulzar": "Urdu (Gulzar-e-Urdu)",
    "nai": "Urdu (Nai Awaz)",
    "awaz": "Urdu (Nai Awaz)",
}

def title_from_path(path):
    base = os.path.basename(path).replace(".pdf", "")
    base = re.sub(r"class-?\d+[-_]", "", base, flags=re.I)
    base = re.sub(r"^(science|maths|mathematics|physics|chemistry|biology|social|english|hindi|sanskrit)-?", "", base, flags=re.I)
    base = base.replace("-", " ").replace("_", " ").strip()
    base = re.sub(r"\s+", " ", base).title()
    # If the subject directory name is not in the cleaned title, prepend it
    dir_parts = path.split(os.sep)
    for i, p in enumerate(dir_parts):
        if p.lower() in ("sanskrit", "hindi") and p.lower() not in base.lower():
            base = f"{p.title()} {base}"
            break
    return base or os.path.basename(path)

def subject_from_dir(d):
    d = (d or "").lower().replace("-", " ").replace("_", " ")
    for k, v in SUBJECTS.items():
        if k in d:
            return v
    return None

def count_pages(path):
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        return None

def scan(force=False):
    conn = get_conn()
    existing_map = {r["path"]: r["id"] for r in conn.execute("SELECT id, path FROM pdfs").fetchall()}
    added = 0
    for root, dirs, files in os.walk(PDF_ROOT):
        dirs[:] = [d for d in dirs if d.lower() != "extracted"]
        for fn in files:
            if not fn.lower().endswith(".pdf"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, PDF_ROOT)
            parts = rel.split(os.sep)
            cls = parts[0] if parts else ""
            m = re.match(r"class-?(\d+)", cls, re.I)
            cls_num = m.group(1) if m else cls
            # Subject comes from the subdirectory (second path component)
            subj_dir = parts[1] if len(parts) > 1 else ""
            subject = subject_from_dir(subj_dir) or subject_from_dir(rel)
            title = title_from_path(full)
            pages = count_pages(full)
            words = None  # filled lazily via /api/pdfs/<id>/stats

            if full in existing_map:
                if force:
                    conn.execute(
                        "UPDATE pdfs SET class=?, subject=?, title=?, pages=?, words=? WHERE path=?",
                        (cls_num, subject, title, pages, words, full),
                    )
            else:
                conn.execute(
                    "INSERT INTO pdfs(class,subject,title,path,pages,words) VALUES(?,?,?,?,?,?)",
                    (cls_num, subject, title, full, pages, words),
                )
                added += 1
    commit(conn)
    conn.close()
    return added