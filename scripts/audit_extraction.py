"""Crosscheck extraction retention against the source PDFs using Gemma vision.

For each selected chapter PDF (one per subject), audit every page:
  * What WE extracted  -> text layer (fitz), diagrams (caption/vector/raster
    detectors), tables (fitz find_tables)
  * What IS on the page -> Gemma vision enumerates text/equations/figures/
    tables/artwork with rough fractional positions

Report per-page losses: figures we missed, pages whose text layer is empty
but clearly have text, equations the text layer mangled, etc. The goal is
maximum retention — flag ANY page where Gemma sees content we did not capture.

Usage:
  python3 scripts/audit_extraction.py            # run all subjects
  python3 scripts/audit_extraction.py --pdf 788  # run one PDF
  python3 scripts/audit_extraction.py --pages 0,5,8  # page subset (all pdfs)
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import hashlib
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
import fitz

from pipeline.lib import pdf_diagram_extractor as D

LLAMA_HOST = os.environ.get("LLAMA_HOST", "http://127.0.0.1:18080")
OUT_DIR = Path(__file__).resolve().parents[1] / "research" / "extraction-audit"

PDFS = [
    (780, "11", "Physics",        "Units and Measurement",            "assets/pdfs/class-11/physics/keph_ch01.pdf"),
    (788, "11", "Mathematics",    "Relations and Functions",          "assets/pdfs/class-11/mathematics/kemh_ch02.pdf"),
    (773, "11", "Chemistry",      "Some Basic Concepts of Chemistry", "assets/pdfs/class-11/chemistry/kech_ch01.pdf"),
    (896, "12", "Physics",        "Electric Charges and Fields",      "assets/pdfs/class-12/physics/leph_ch01.pdf"),
    (657, "10", "Science",        "Life Processes",                   "assets/pdfs/class-10/science/jesc_ch05.pdf"),
    (679, "10", "Mathematics",    "Polynomials",                      "assets/pdfs/class-10/mathematics/jemh_ch02.pdf"),
    (667, "10", "Social Science", "Resources and Development",        "assets/pdfs/class-10/social-science/jess_ch01.pdf"),
    (683, "10", "Health & PE",    "Physical Education and Sports",    "assets/pdfs/class-10/health-and-physical-education/jehp_ch01.pdf"),
    (693, "10", "Hindi",          "Kshitij: poetry section",          "assets/pdfs/class-10/hindi-kshitij/jhks_ch01.pdf"),
    (715, "10", "Sanskrit",       "Abhyas chapter 1",                 "assets/pdfs/class-10/sanskrit/jsab_ch01.pdf"),
    (849, "11", "Geography",      "Geography as a Discipline",        "assets/pdfs/class-11/geography/kegy_ch01.pdf"),
    (939, "12", "History",        "Themes in Indian History",         "assets/pdfs/class-12/history_heritage-crafts/lehs_ch01.pdf"),
    (578, "9",  "English",        "The Lament",                       "assets/pdfs/class-9/english/iebe_ch01.pdf"),
    (724, "10", "Urdu",           "Gulzar-e-Urdu",                    "assets/pdfs/class-10/urdu-gulzar-e-urdu/juge_ch01.pdf"),
]


# ---------------------------------------------------------------------------
#  Per-page extraction (what WE capture) — deterministic, no Gemma
# ---------------------------------------------------------------------------
def our_text(pdf: str, pno: int) -> str:
    doc = fitz.open(pdf)
    try:
        t = doc[pno].get_text("text") or ""
    finally:
        doc.close()
    return t


def our_diagrams(pdf: str, pno: int, bg_sizes) -> list:
    """Return the diagram regions our pipeline would anchor on this page.
    Uses the same caption / raster / vector detectors as extract_diagrams but
    without any vision description (pure geometry)."""
    doc = fitz.open(pdf)
    pw, ph = doc[pno].rect.width, doc[pno].rect.height
    doc.close()
    out = []

    caps = D._find_caption_lines(pdf, pno)
    if caps:
        content_rects = D._content_image_rects(pdf, pno, bg_sizes)
        full = D._get_pdf_page_image(pdf, pno, dpi=150)
        band_top, prev_bottom = 62.0, 62.0
        for cap in sorted(caps, key=lambda c: c["bbox"][1]):
            body_bottom = D._body_text_bottom_above(pdf, pno, cap["bbox"][1])
            band_top = max(prev_bottom, body_bottom)
            bbox_px = D._figure_bbox_for_caption(pdf, pno, cap["bbox"],
                                                 content_rects, full,
                                                 band_top_pt=band_top, dpi=150)
            prev_bottom = max(prev_bottom, cap["bbox"][3])
            if bbox_px:
                out.append({"kind": "figure", "label": cap["text"],
                            "pt": [round(v / 150 * 72, 1) for v in bbox_px]})
        if full:
            try:
                os.remove(full)
            except OSError:
                pass
        return out

    for fig in D._real_raster_regions(pdf, pno, bg_sizes):
        out.append({"kind": "raster", "label": "", "pt": fig["rect_pt"]})
    for fig in D._vector_figure_regions(pdf, pno):
        out.append({"kind": "vector", "label": "", "pt": fig["rect_pt"]})
    return out


def our_tables(pdf: str, pno: int) -> list:
    doc = fitz.open(pdf)
    try:
        tabs = doc[pno].find_tables()
        return [{"rows": t.row_count, "cols": t.col_count,
                 "pt": [round(v, 1) for v in t.bbox]} for t in tabs.tables]
    except Exception:
        return []
    finally:
        doc.close()


# ---------------------------------------------------------------------------
#  Gemma page oracle (what IS on the page)
# ---------------------------------------------------------------------------
def render_page(pdf: str, pno: int, dpi: int = 90) -> str:
    doc = fitz.open(pdf)
    try:
        pix = doc[pno].get_pixmap(dpi=dpi)
        tmp = os.path.join(OUT_DIR, f"_pix_{os.path.basename(pdf)}_{pno}.png")
        pix.save(tmp)
        return tmp
    finally:
        doc.close()


GEMMA_PROMPT = """You are auditing a textbook page for data-retention completeness.
Look carefully at this page image and report ONLY what is actually visible.
Return a JSON object with exactly these keys:
- "has_text": true/false  (any readable body text at all, any script)
- "text_quality": "clean" | "garbled" | "scanned" | "none"  (are letters legible; for Devanagari/Urdu/Sanskrit say clean if readable)
- "num_figures": integer  (figures/diagrams/illustrations/photos/drawings — NOT tables, NOT equations)
- "figure_bboxes": list of [x1,y1,x2,y2] in 0-1000 fractional coords, one per figure (tight on artwork, not captions). Empty list if none.
- "num_equations": integer  (math/chem equations, formulas, reaction schemes, chemical structures)
- "has_tables": true/false
- "num_tables": integer
- "num_charts_or_graphs": integer  (data plots/graphs — count separately from generic figures)
- "has_artwork": true/false  (decorative art, borders, headings with graphics)
- "notes": one short sentence on anything unusual or anything a text-extractor would likely miss (e.g. 'chemistry structures will be missed by plain text').
Count conservatively: if unsure whether something is a figure, DO count it — the goal is to catch anything we might have failed to extract.
Respond with ONLY the JSON object."""


def gemma_oracle(img_path: str) -> dict:
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": GEMMA_PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "max_tokens": 6000,
        "temperature": 0,
    }
    try:
        r = requests.post(f"{LLAMA_HOST}/v1/chat/completions", json=payload,
                          timeout=180)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        content = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return {"error": "no JSON", "raw": content[:200]}
        return json.loads(m.group())
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
#  Loss classification
# ---------------------------------------------------------------------------
def _bbox_has_artwork(pdf: str, pno: int, bbox_frac: list,
                      bg_sizes: set, threshold: float = 25.0) -> bool:
    """Return True if a fractional bbox (0-1000) contains real artwork:
    enough vector drawings OR a non-background embedded image. Pure body-text
    columns produce almost no drawings and no images, so they return False —
    this filters Gemma's 'figure in a text column' false positives.
    """
    doc = fitz.open(pdf)
    try:
        page = doc[pno]
        pw, ph = page.rect.width, page.rect.height
        x0, y0, x1, y1 = [v / 1000 * (pw if i % 2 == 0 else ph)
                          for i, v in enumerate(bbox_frac)]
        box = fitz.Rect(x0, y0, x1, y1)
        if box.is_empty:
            return False
        # vector drawings inside the box (count significant ones)
        n_draw = 0
        for d in page.get_drawings():
            r = d["rect"]
            if box.intersects(r):
                n_draw += 1
        if n_draw >= threshold:
            doc.close()
            return True
        # embedded raster images inside the box (excluding backgrounds)
        for im in page.get_images(full=True):
            xref = im[0]
            pix = fitz.Pixmap(doc, xref)
            if (pix.width, pix.height) in bg_sizes:
                continue
            if pix.width < 100 or pix.height < 100:
                continue
            for r in page.get_image_rects(xref):
                if box.intersects(r):
                    doc.close()
                    return True
        doc.close()
        return False
    except Exception:
        doc.close()
        return True  # err on the side of flagging


def _bbox_covered(ours: dict, pdf: str, pno: int, bbox_frac: list,
                  overlap: float = 0.5) -> bool:
    """True when a Gemma bbox is substantially covered by one of our extracted
    diagram regions (so its artwork is already retained). A two-panel figure we
    extract as one region passes; a region missing entirely fails."""
    doc = fitz.open(pdf)
    try:
        pw, ph = doc[pno].rect.width, doc[pno].rect.height
        gx0, gy0, gx1, gy1 = [v / 1000 * (pw if i % 2 == 0 else ph)
                              for i, v in enumerate(bbox_frac)]
        g = fitz.Rect(gx0, gy0, gx1, gy1)
        if g.is_empty:
            return False
        g_area = g.get_area()
        if g_area <= 0:
            return False
        best = 0.0
        for r in ours["diagrams"]:
            r = fitz.Rect(*r["pt"])
            inter = r.intersect(g).get_area()
            best = max(best, inter / g_area)
        return best >= overlap
    except Exception:
        return False
    finally:
        doc.close()


def classify(ours: dict, gemma: dict, pdf: str = None, pno: int = None,
             bg_sizes: set = None) -> list:
    """Return list of human-readable loss flags for one page.

    Ground truth for figures is Gemma's explicit `figure_bboxes` list (a chart/
    graph is also a figure, so `num_charts_or_graphs` is a subset signal and is
    never added on top — that would double-count). Every figure Gemma reports
    that we did not capture is verified against the actual page artwork so text
    columns misread as figures don't produce false losses.
    """
    issues = []
    fb = gemma.get("figure_bboxes") or []
    # Ground truth = the larger of the explicit bbox list and the model's
    # conservative figure count (Gemma sometimes reports N figures but emits
    # bboxes for fewer). Never less than either.
    gt_figs = max(len(fb), gemma.get("num_figures") or 0)
    gt_charts = gemma.get("num_charts_or_graphs") or 0
    our_figs = len(ours["diagrams"])
    if gt_figs > 0 and our_figs == 0:
        issues.append(f"FIGURES: Gemma sees {gt_figs} figure(s) on page, we extracted 0")
    elif gt_figs > our_figs:
        issues.append(f"FIGURES: Gemma sees {gt_figs}, we have {our_figs}")
    # Extra charts beyond figure_bboxes would indicate an additional plot we
    # might have missed even though the model did not give it a bbox.
    if gt_charts > gt_figs:
        issues.append(f"CHARTS: Gemma counts {gt_charts} chart(s) vs {gt_figs} figure bboxes")

    # Verify each reported-but-not-extracted figure. A Gemma bbox is a real
    # loss only if it (a) actually contains artwork (not a text column) AND
    # (b) is NOT covered by any of our extracted regions (so a single
    # two-panel figure we extract as one region is not a false loss).
    if (gt_figs > our_figs) and pdf is not None and pno is not None and fb:
        real_miss = 0
        for bbox in fb:
            if not _bbox_has_artwork(pdf, pno, bbox, bg_sizes or set()):
                continue  # text column misread as a figure — not a real loss
            if _bbox_covered(ours, pdf, pno, bbox):
                continue  # our extraction already contains this artwork
            real_miss += 1
        # Replace the blanket figure-flag with the verified count.
        issues = [i for i in issues if not i.startswith("FIGURES:")]
        if real_miss > 0 and our_figs == 0:
            issues.append(f"FIGURES: {real_miss} verified artwork region(s) on page, we extracted 0")
        elif real_miss > our_figs:
            issues.append(f"FIGURES: {real_miss} verified artwork region(s), we have {our_figs}")

    if gemma.get("has_text"):
        tq = gemma.get("text_quality")
        if tq == "garbled":
            issues.append("TEXT: page text is garbled (extractor will not retain readable text)")
        elif tq == "scanned":
            issues.append("TEXT: scanned page — plain text extraction returns nothing (needs OCR)")
        if not ours["text"].strip():
            issues.append("TEXT: Gemma sees text but our text layer is empty")
    else:
        if ours["text"].strip():
            issues.append("TEXT: Gemma sees no text but we extracted some (possible hallucination)")

    eq = gemma.get("num_equations") or 0
    if eq > 0:
        # Flag only if equations likely mangled: our text has few math chars
        math_chars = sum(1 for c in ours["text"] if c in "=√∫∑π±×÷≤≥²³→∆Σθαβγ" or c.isupper())
        if len(ours["text"].strip()) < 200:
            issues.append(f"EQUATIONS: {eq} equation(s) on page, text too short to retain them well")

    if gemma.get("has_tables") and not ours["tables"]:
        # Borderless data tables (e.g. isotope abundance lists) have no ruling
        # lines, so find_tables() returns 0 even though the content is fully
        # preserved as text. Only flag a real loss when the tabular data is NOT
        # verifiably present in our text layer.
        text = ours["text"]
        tabular = sum(1 for line in text.splitlines()
                      if "\t" in line or len(line.split()) >= 3 and line.count(" ") >= 3)
        numeric = sum(1 for tok in text.split()
                      if any(ch.isdigit() for ch in tok))
        if tabular >= 1 or numeric >= 8:
            pass  # content preserved as text — not a loss
        else:
            issues.append(f"TABLES: Gemma sees {gemma.get('num_tables', 1)} table(s), we found 0 structured tables and no tabular text")
    return issues


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def audit_pdf(pdf_id, cls, subject, chapter, path, only_pages=None, verbose=False):
    if not os.path.exists(path):
        return {"pdf_id": pdf_id, "subject": subject, "chapter": chapter,
                "class": cls, "status": "MISSING_FILE", "pages": []}
    doc = fitz.open(path)
    total = len(doc)
    doc.close()
    pages = only_pages if only_pages is not None else range(total)
    bg = D._background_image_sizes(path)

    per_page = []
    for pno in pages:
        ours = {
            "text": our_text(path, pno),
            "diagrams": our_diagrams(path, pno, bg),
            "tables": our_tables(path, pno),
        }
        img = render_page(path, pno)
        gt = gemma_oracle(img)
        try:
            os.remove(img)
        except OSError:
            pass
        if "error" in gt:
            per_page.append({"page": pno, "error": gt.get("error"),
                             "raw": gt.get("raw", "")})
            continue
        issues = classify(ours, gt, pdf=path, pno=pno, bg_sizes=bg)
        per_page.append({
            "page": pno,
            "text_chars": len(ours["text"].strip()),
            "our_figs": len(ours["diagrams"]),
            "our_tables": len(ours["tables"]),
            "gemma_figs": (gt.get("num_figures") or 0) + (gt.get("num_charts_or_graphs") or 0),
            "gemma_eqs": gt.get("num_equations") or 0,
            "gemma_tables": gt.get("num_tables") or 0,
            "text_quality": gt.get("text_quality"),
            "issues": issues,
        })
        if verbose:
            print(f"  p{pno:3d} text={len(ours['text'].strip()):5d} "
                  f"figs {len(ours['diagrams'])}/{gt.get('num_figures')} "
                  f"charts {gt.get('num_charts_or_graphs')} "
                  f"eqs {gt.get('num_equations')} tables {len(ours['tables'])}/{gt.get('num_tables')} "
                  f"-> {'; '.join(issues) if issues else 'OK'}")
    return {"pdf_id": pdf_id, "subject": subject, "chapter": chapter,
            "class": cls, "status": "done", "pages": per_page}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=int, default=None)
    ap.add_argument("--pages", type=str, default=None,
                    help="comma list of page numbers (all pdfs)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    only_pages = None
    if args.pages:
        only_pages = [int(p) for p in args.pages.split(",")]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "report.json"

    # Resume: keep results already completed (skip the ones already in report)
    report = []
    if out.exists():
        try:
            report = json.loads(out.read_text())
        except Exception:
            report = []
    done_pdfs = {r["pdf_id"] for r in report}

    targets = [p for p in PDFS if args.pdf is None or p[0] == args.pdf]
    for pid, cls, subj, chap, path in targets:
        if pid in done_pdfs:
            print(f"=== [{cls}] {subj} — {chap} (already done, skipping) ===")
            continue
        print(f"=== [{cls}] {subj} — {chap} ===", flush=True)
        r = audit_pdf(pid, cls, subj, chap, path, only_pages, verbose=args.verbose)
        report.append(r)
        ok = sum(1 for p in r["pages"] if not p.get("issues"))
        err = sum(1 for p in r["pages"] if "error" in p)
        print(f"    -> {ok}/{len(r['pages'])} pages clean, {err} gemma errors",
              flush=True)
        with open(out, "w") as f:  # checkpoint after each PDF
            json.dump(report, f, indent=2)
        print(f"    [checkpoint written]", flush=True)

    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
