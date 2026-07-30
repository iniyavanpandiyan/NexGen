#!/usr/bin/env python3
"""Scrape NCERT PDFs from the official ncert.nic.in source.

Reads the curriculum mapping from studio.db, downloads all chapter PDFs
for Classes 9-12 using the verified URL pattern:

  https://ncert.nic.in/textbook/pdf/{CODE}{NNN}.pdf

Where CODE is a 4-char book code and NNN = 100 + chapter_number for Part I,
or 200 + chapter_number for Part II (if the book has a second volume).

Part II books: Chemistry, Physics (Class 11 & 12), Maths (12 only),
Geography (11 & 12), History/Heritage Crafts (12 only).

Usage:
  python3 pipeline/lib/ncert_nic_scraper.py              # download all classes
  python3 pipeline/lib/ncert_nic_scraper.py --class 9     # only class 9
  python3 pipeline/lib/ncert_nic_scraper.py --code iesc   # specific book
  python3 pipeline/lib/ncert_nic_scraper.py --dry         # just print what would happen
  python3 pipeline/lib/ncert_nic_scraper.py --resume      # skip already-downloaded PDFs
"""

import os, sys, time, argparse, logging, re
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_ROOT = PROJECT_ROOT / "assets" / "pdfs"
DB_PATH = PROJECT_ROOT / "webapp" / "backend" / "studio.db"

# Rate limiting — be nice to ncert.nic.in
DELAY_SECONDS = 0.5
MAX_RETRIES = 3
RETRY_DELAY = 5

# Actual chapter counts per book (verified against ncert.nic.in 2026-07-11).
# These OVERRIDE the DB curriculum view which has stale values.
# For 2-part books: (part1_chapters, part2_chapters).
# For 1-part books: (total_chapters, 0).
ACTUAL_CHAPTERS = {
    # Class 9 — NCF 2026 curriculum
    "iesc": (13, 0),  # Science
    "iemh": (8, 0),   # Mathematics
    "iest": (9, 0),   # Social Science
    "iebe": (8, 0),   # English
    "iehp": (6, 0),   # Health & Physical Education
    "ihsh": (16, 0),  # Sanskrit
    "ihga": (12, 0),  # Hindi
    "iuju": (12, 0),  # Urdu
    "iekv": (12, 0),  # Vocational
    "iemr": (12, 0),  # Language
    # Class 10
    "jesc": (13, 0),  # Science
    "jemh": (14, 0),  # Mathematics (DB=8, actual=14)
    "jess": (7, 0),   # Social Science (DB=8, actual=7)
    "jehp": (12, 0),  # HPE (DB=6, actual=12)
    "jhkr": (3, 0),   # Hindi Kritika (DB=5, actual=3)
    "jhks": (12, 0),  # Hindi Kshitij (DB=17, actual=12)
    "jhva": (12, 0),  # Hindi Vyakaran (DB=10, actual=12)
    "jsab": (14, 0),  # Sanskrit (DB=10, actual=14)
    "juge": (12, 0),  # Urdu Gulzar (DB=10, actual=12)
    "jujp": (19, 0),  # Urdu Jaan Pehchan (DB=10, actual=19)
    "june": (14, 0),  # Urdu Nai Awaz (DB=10, actual=14)
    "jusc": (16, 0),  # Sanskrit Shemushi (DB=10, actual=16)
    "jusr": (9, 0),   # Sanskrit Vyakaran (DB=10, actual=9)
    # Class 11 — 2-part books
    "kech": (6, 3),   # Chemistry
    "keph": (7, 7),   # Physics
    "kemh": (14, 0),  # Mathematics
    "kehp": (11, 0),  # HPE (DB=6, actual=11)
    "kegy": (6, 14),  # Geography
    "kehs": (7, 0),   # History
    # Class 12 — 2-part books
    "lech": (5, 5),   # Chemistry
    "leph": (8, 6),   # Physics
    "lemh": (6, 7),   # Mathematics
    "legy": (8, 9),   # Geography
    "lehs": (4, 4),   # History
}


def get_curriculum(conn, class_filter=None, code_filter=None):
    """Fetch curriculum rows from the DB view."""
    import sqlite3
    sql = "SELECT * FROM curriculum"
    params = []
    conditions = []
    if class_filter:
        conditions.append("class = ?")
        params.append(str(class_filter))
    if code_filter:
        conditions.append("code = ?")
        params.append(code_filter)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def slugify(subject):
    """Turn 'Social Science' -> 'social-science', 'Hindi (Kritika)' -> 'hindi_kritika'."""
    s = subject.lower().strip()
    s = re.sub(r"[()]", "", s)
    s = re.sub(r"\s*&\s*", "-and-", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "_", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def get_chapters_for_code(code, db_chapters):
    """Get actual chapter counts, respecting Part I/II split.

    Returns a list of (ch_number, part_label) tuples.
    ch_number is the PDF URL suffix (101, 102, ..., 201, 202, ...).
    part_label is 'part1' or 'part2' for the filename.
    """
    chapters = []
    if code in ACTUAL_CHAPTERS:
        part1, part2 = ACTUAL_CHAPTERS[code]
        for ch in range(1, part1 + 1):
            chapters.append((100 + ch, "part1"))
        for ch in range(1, part2 + 1):
            chapters.append((200 + ch, "part2"))
    else:
        # Fallback to DB chapter count for unknown codes
        for ch in range(1, db_chapters + 1):
            chapters.append((100 + ch, ""))
    return chapters


def download_pdf(url, dest_path, dry=False, resume=False):
    """Download a PDF from url to dest_path. Returns True on success."""
    if dest_path.exists():
        if resume:
            return True  # skip, already have it
        log.info("  ✓ already exists: %s", dest_path.name)
        return True

    if dry:
        log.info("  [dry] would download: %s -> %s", url, dest_path)
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, MAX_RETRIES + 1):
        rv = os.system(
            f'curl -sSfL -o "{dest_path}" --max-time 30 --retry 2 "{url}" 2>&1'
        )
        if rv == 0:
            size = dest_path.stat().st_size
            if size > 1000:  # sanity: > 1KB
                log.info("  ✓ %s (%.1f KB)", dest_path.name, size / 1024)
                return True
            else:
                log.warning("  ⚠ too small (%d B), retrying...", size)
                dest_path.unlink(missing_ok=True)
        else:
            log.warning("  ✗ attempt %d/%d failed: %s", attempt, MAX_RETRIES, url)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    dest_path.unlink(missing_ok=True)
    return False


def main():
    parser = argparse.ArgumentParser(description="Scrape NCERT PDFs from ncert.nic.in")
    parser.add_argument("--class", dest="class_filter", help="Only download this class (9, 10, 11, 12)")
    parser.add_argument("--code", help="Only download this book code (e.g. iesc)")
    parser.add_argument("--dry", action="store_true", help="Dry run — print what would happen")
    parser.add_argument("--resume", action="store_true", help="Skip already-downloaded files")
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS, help=f"Delay between downloads (default {DELAY_SECONDS}s)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found at %s", DB_PATH)
        log.error("Run the webapp backend first to create the DB and curriculum view.")
        sys.exit(1)

    import sqlite3

    conn = sqlite3.connect(str(DB_PATH))
    rows = get_curriculum(conn, class_filter=args.class_filter, code_filter=args.code)

    if not rows:
        log.error("No curriculum rows found%s%s",
                   f" for class {args.class_filter}" if args.class_filter else "",
                   f" for code {args.code}" if args.code else "")
        sys.exit(1)

    log.info("Found %d curriculum entries to scrape", len(rows))

    total = 0
    success = 0
    failed = 0

    for row in rows:
        cls = row["class"]
        subject = row["subject"]
        code = row["code"]
        db_chapters = row["chapters"]
        url_template = row["chapter_url_pattern"]

        cls_dir = f"class-{cls}"
        subj_dir_name = slugify(subject)
        out_dir = PDF_ROOT / cls_dir / subj_dir_name
        out_dir.mkdir(parents=True, exist_ok=True)

        chapters = get_chapters_for_code(code, db_chapters)
        log.info("[%s] %s (%s) — %d PDFs → %s", cls, subject, code, len(chapters), out_dir)

        part1_count = max(c for c, p in chapters if p == "part1") - 100 if any(p == "part1" for _, p in chapters) else 0

        for seq, (ch_num, part) in enumerate(chapters, 1):
            url = url_template.replace("{ch}", str(ch_num))

            if part == "part1":
                ch_label = ch_num - 100
            elif part == "part2":
                ch_label = part1_count + (ch_num - 200)
            else:
                ch_label = ch_num - 100
            fname = f"{code}_ch{ch_label:02d}.pdf"

            dest = out_dir / fname

            ok = download_pdf(url, dest, dry=args.dry, resume=args.resume)
            if ok:
                success += 1
            else:
                failed += 1
                log.error("  ✗ FAILED (all attempts): %s", url)

            total += 1
            time.sleep(args.delay)

    conn.close()

    log.info("=" * 50)
    log.info("Done: %d/%d successful, %d failed", success, total, failed)
    log.info("PDFs saved under: %s", PDF_ROOT)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())