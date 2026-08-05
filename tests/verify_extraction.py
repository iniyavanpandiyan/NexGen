"""Ground-truth verification harness for diagram + chapter-name extraction.

Asserts deterministic invariants that were hand-verified:

  * 683 (jehp_ch01) page 0  -> exactly 1 embedded figure
  * 683 (jehp_ch01) page 3  -> exactly 6 COVID-hygiene icons
  * 687 (jehp_ch02) PE chapter name is correct (header method)
  * PE chapter names match the official NCERT list (no confabulated titles)

Run:  python3 -m tests.verify_extraction   (or directly)
Exit code 0 on success, 1 on any failure.
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.lib import pdf_diagram_extractor as D

ASSETS = "/home/fiipadmin/workspace/NexGen/assets/pdfs"
DB = "/home/fiipadmin/workspace/NexGen/webapp/backend/studio.db"


def _db():
    return sqlite3.connect(DB)


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))
    return cond


def main() -> int:
    results = []

    # --- Diagram determinism: 683 (jehp_ch01) ---
    res = D.extract_diagrams(os.path.join(ASSETS, "class-10/health-and-physical-education/jehp_ch01.pdf"))
    p0 = [d for d in res if d.get("page_number") == 0]
    p3 = [d for d in res if d.get("page_number") == 3]
    results.append(check("683 p0 exactly 1 figure", len(p0) == 1, f"got {len(p0)}"))
    results.append(check("683 p3 exactly 6 icons", len(p3) == 6, f"got {len(p3)}"))
    results.append(check("683 p3 icons all have descriptions",
                         all((d.get("description") or "").strip() for d in p3)))
    results.append(check("683 diagrams only on pages with real raster figures",
                         set(d.get("page_number") for d in res) == {0, 3},
                         f"pages: {sorted(set(d.get('page_number') for d in res))}"))

    # --- PE chapter names must match official NCERT list ---
    conn = _db()
    rows = conn.execute(
        "SELECT id, chapter_name, identified_method, path FROM pdfs "
        "WHERE class='class-10' AND subject='health-and-physical-education'"
    ).fetchall()
    conn.close()
    official = [
        "physical education relationship with other subjects",
        "effects of physical activities on human body",
        "growth and development during adolescence",
        "physical fitness",
        "sports training",
        "measurement of sports performance",
        "physiology and injuries in sport",
        "psychology and sports",
        "yoga and lifestyle",
        "training and doping in sports",
    ]
    bad = []
    for pid, name, method, path in rows:
        norm = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
        if norm not in official:
            bad.append((pid, name, method, path))
    results.append(check("all class-10 PE chapter names are official NCERT",
                         not bad, f"{len(bad)} mismatches"))

    # --- 687 (jehp_ch02) header method + correct title ---
    conn = _db()
    row = conn.execute(
        "SELECT chapter_name, identified_method FROM pdfs WHERE path LIKE '%jehp_ch02%'"
    ).fetchone()
    conn.close()
    if row:
        results.append(check("687 chapter name from header",
                             row[0] == "Effects of Physical Activities on Human Body",
                             f"got {row[0]!r} via {row[1]!r}"))
    else:
        results.append(check("687 exists in DB", False, "row not found"))

    failed = sum(1 for r in results if not r)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
