"""Extract diagrams, figures, and illustrations from PDF textbook pages.

Pipeline:
  1. Render each page to a high-res PNG (via pdftoppm / PyMuPDF)
  2. Extract embedded images from the PDF binary (via PyMuPDF)
  3. Send page image to Gemma 4 vision (via llama-server) for diagram detection
  4. Crop out diagram regions and save as individual PNG files
  5. Return structured list of {page, description, image_path, bbox, ...}

Requires:
  - PyMuPDF (pip install pymupdf)
  - llama-server running with Gemma 4 E2B (vision model)
  - poppler-utils (apt install poppler-utils)
"""

import os, json, base64, subprocess, tempfile, re
from pathlib import Path
from typing import Optional

LLAMA_HOST = os.environ.get("LLAMA_HOST", "http://127.0.0.1:8082")
# Running server is on port 8082 with Gemma 4 E2B vision model
EXTRACT_DIR = Path(__file__).resolve().parents[2] / "assets" / "pdfs" / "extracted"


def _get_pdf_page_image(pdf_path: str, page_num: int, dpi: int = 150) -> Optional[str]:
    """Render a single PDF page to a PNG image file.
    Returns path to the rendered PNG, or None.

    Renders from a watermark-free copy of the source PDF when possible so
    diagram crops never contain the 'Not to be Republished' overlay.
    """
    render_pdf = _clean_pdf_path(pdf_path)
    out_dir = tempfile.mkdtemp(prefix="pdf-diagram-")
    stem = Path(render_pdf).stem
    out_path = os.path.join(out_dir, f"{stem}_p{page_num}.png")

    try:
        subprocess.run(
            ["pdftoppm", "-png", "-f", str(page_num + 1), "-l", str(page_num + 1),
             "-r", str(dpi), "-singlefile", render_pdf, os.path.join(out_dir, f"{stem}_p{page_num}")],
            capture_output=True, timeout=60, check=True
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        import fitz
        doc = fitz.open(render_pdf)
        page = doc[page_num]
        pix = page.get_pixmap(dpi=dpi)
        pix.save(out_path)
        doc.close()
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except ImportError:
        pass

    return None


def _extract_embedded_images(pdf_path: str, page_num: int, output_dir: str) -> list:
    """Extract all embedded images from a single PDF page using PyMuPDF.
    Returns list of {path, bbox, width, height}.
    """
    import fitz
    results = []
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    page_rect = page.rect

    for idx, img_info in enumerate(page.get_images(full=True)):
        xref = img_info[0]
        base_img = doc.extract_image(xref)
        img_bytes = base_img["image"]
        ext = base_img["ext"]

        # Get image position on page
        for img_block in page.get_image_info():
            if img_block.get("xref") == xref or img_block.get("name", "").endswith(str(xref)):
                bbox = [img_block["bbox"][i] for i in range(4)]
                break
        else:
            bbox = None

        fname = f"embedded_p{page_num}_{idx}.{ext}"
        fpath = os.path.join(output_dir, fname)
        with open(fpath, "wb") as f:
            f.write(img_bytes)

        w, h = base_img.get("width", 0), base_img.get("height", 0)
        results.append({
            "path": fpath,
            "bbox": bbox,
            "width": w,
            "height": h,
            "ext": ext,
        })

    doc.close()
    return results


def _gemma_analyze_image(image_path: str, task: str = "diagram") -> dict:
    """Send an image to Gemma 4 via llama-server for analysis.
    For task='diagram', returns JSON with diagram regions + labels.
    """
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    if task == "diagram":
        prompt = (
            "You are a science education expert analyzing a textbook page image. "
            "Return ONLY valid JSON (no other text). "
            "If the page contains any diagram, figure, chart, table, graph, or illustration, "
            "return a JSON object with a 'diagrams' array. Each entry in the array must have:\n"
            "- 'label': the figure label if visible (e.g., 'Fig. 6.1' or 'Figure 2'), or empty string\n"
            "- 'title': the title or caption of the diagram\n"
            "- 'description': a 1-2 sentence description of what the diagram shows\n"
            "- 'bbox': bounding box in fractional coordinates [x1, y1, x2, y2] where values are 0-1000, "
            "representing the diagram region on the page. The box must tightly enclose ONLY the "
            "diagram artwork, NOT its caption text below it and NOT surrounding text columns. "
            "Use the layout composition (rule lines, borders, whitespace gaps) to isolate the figure "
            "as precisely as possible.\n"
            "- 'caption_bbox': optional bounding box [x1,y1,x2,y2] of the figure caption text "
            "immediately below the diagram, if present (same 0-1000 scale).\n\n"
            "Be generous — include tables, graphs, charts, anatomical drawings, circuit diagrams, "
            "chemical structures, maps, flowcharts as diagrams.\n"
            "EXCLUDE: QR codes, page headers/footers, logos, page numbers, navigation icons, "
            "and pure text passages.\n"
            "If there are NO diagrams on this page, return: {\"diagrams\": []}"
        )
    elif task == "full":
        prompt = (
            "You are analyzing a textbook page extract. Return a JSON object with fields:\n"
            "- diagrams: list of diagrams/figures with descriptions (or [])\n"
            "- activities: any activity boxes or experiments (or [])\n"
            "- key_concepts: main concepts illustrated (or [])\n"
            "If none, use empty list."
        )
    else:
        prompt = task

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    try:
        resp = __import__("requests").post(
            f"{LLAMA_HOST}/v1/chat/completions",
            json=payload, timeout=120,
        )
        if resp.status_code != 200:
            return {"error": f"API error {resp.status_code}: {resp.text[:200]}"}
        content = resp.json()["choices"][0]["message"]["content"]
        if task == "diagram" or task == "full":
            # Try to extract JSON from response
            try:
                parsed = json.loads(content)
                return parsed
            except json.JSONDecodeError:
                pass
            # Fallback: try to find JSON in markdown code blocks
            m = re.search(r'\{.*"diagrams".*\}', content, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group())
                    return parsed
                except json.JSONDecodeError:
                    pass
            m2 = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if m2:
                try:
                    parsed = json.loads(m2.group(1))
                    return parsed
                except json.JSONDecodeError:
                    pass
        return {"analysis": content}
    except Exception as e:
        return {"error": str(e)}


def _crop_diagram_regions(page_image: str, descriptions: list, output_dir: str) -> list:
    """Crop diagram regions from a full page render using heuristic layout analysis.
    Returns list of {path, bbox, width, height}.
    """
    from PIL import Image
    img = Image.open(page_image)
    pw, ph = img.size
    results = []

    for desc in descriptions:
        bbox = desc.get("bbox")
        if bbox and len(bbox) == 4:
            x1, y1, x2, y2 = bbox
            scale_x = pw / 1000
            scale_y = ph / 1000
            left = int(x1 * scale_x)
            top = int(y1 * scale_y)
            right = int(x2 * scale_x)
            bottom = int(y2 * scale_y)
            crop = img.crop((left, top, right, bottom))
            if crop.size[0] < 50 or crop.size[1] < 50:
                continue
            fname = f"crop_{desc.get('page', 0)}_{len(results)}.png"
            fpath = os.path.join(output_dir, fname)
            crop.save(fpath)
            results.append({
                "path": fpath,
                "bbox": [left, top, right, bottom],
                "width": crop.size[0],
                "height": crop.size[1],
            })

    img.close()
    return results


def _refine_bbox_with_opencv(page_image: str, bbox_px: list) -> list:
    """Refine a Gemma-provided bbox using OpenCV edge/contour analysis.

    Given a rough bounding box [x1,y1,x2,y2] in pixels on the rendered page,
    detect the diagram's outer contour inside that region and return a tighter
    box that hugs the actual artwork (removing captions / white margins).
    Falls back to the input bbox if OpenCV finds nothing useful.
    """
    import cv2
    import numpy as np
    try:
        img = cv2.imread(page_image)
        if img is None:
            return bbox_px
        x1, y1, x2, y2 = [int(v) for v in bbox_px]
        h, w = img.shape[:2]
        # Clamp to image bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 20 or y2 - y1 < 20:
            return bbox_px

        region = img[y1:y2, x1:x2]
        # Convert to grayscale, blur, detect edges
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        # Adaptive threshold for printed figures (line art, tables)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 11, 5)
        # Invert so ink/artwork is white (foreground)
        thresh = cv2.bitwise_not(thresh)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return bbox_px

        # Union the significant contours instead of taking a single largest one.
        # Taking only the largest contour collapses line-art figures to thin
        # strips (a single axis line or rule), which is the over-cropping bug.
        region_area = (x2 - x1) * (y2 - y1)
        min_area = max(80.0, region_area * 0.001)
        keep = [c for c in contours if cv2.contourArea(c) >= min_area]
        if not keep:
            keep = contours
        xs, ys, xs2, ys2 = x2, y2, x1, y1
        for c in keep:
            bx, by, bw, bh = cv2.boundingRect(c)
            xs, ys = min(xs, x1 + bx), min(ys, y1 + by)
            xs2, ys2 = max(xs2, x1 + bx + bw), max(ys2, y1 + by + bh)

        bw, bh = xs2 - xs, ys2 - ys
        if bw < 20 or bh < 20:
            return bbox_px

        # Add small padding (2% of the box) so edges are not cut
        pad_x = max(3, int(bw * 0.02))
        pad_y = max(3, int(bh * 0.02))
        nx1, ny1 = max(x1, xs - pad_x), max(y1, ys - pad_y)
        nx2, ny2 = min(x2, xs2 + pad_x), min(y2, ys2 + pad_y)

        # Guard against collapsing to thin strips: the refined box must cover at
        # least 30% of the input box's area, otherwise keep the original bbox.
        new_area = (nx2 - nx1) * (ny2 - ny1)
        if new_area < region_area * 0.30:
            return bbox_px
        if (nx2 - nx1) < 40 or (ny2 - ny1) < 40:
            return bbox_px
        return [nx1, ny1, nx2, ny2]
    except Exception:
        return bbox_px


_CLEAN_PDF_CACHE = {}


def _clean_pdf_path(pdf_path: str) -> str:
    """Return a cached path to a watermark-free copy of the PDF.

    NCERT/NCERT-style textbooks layer the 'Not to be Republished' watermark as
    an /Artifact <</Subtype /Watermark>> BDC block in every page's content
    stream. Cropping rendered pages then bakes the watermark into every diagram.
    This removes that block once per source PDF and caches the clean copy so
    region renders (vector figures) never include the watermark.

    Embedded raster figures are preferred anyway (extracted directly from the
    PDF binary via xref), but vector-drawn figures need a clean render.
    """
    key = pdf_path
    if key in _CLEAN_PDF_CACHE and os.path.exists(_CLEAN_PDF_CACHE[key]):
        return _CLEAN_PDF_CACHE[key]

    import hashlib, shutil
    import fitz, re

    digest = hashlib.sha1(os.path.abspath(pdf_path).encode()).hexdigest()[:12]
    clean_dir = Path(EXTRACT_DIR) / "_clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(clean_dir / f"clean_{digest}.pdf")

    src_mtime = os.path.getmtime(pdf_path)
    if os.path.exists(out_path):
        # Reuse cache if source hasn't changed
        if abs(os.path.getmtime(out_path) - src_mtime) < 2:
            _CLEAN_PDF_CACHE[key] = out_path
            return out_path
        try:
            os.remove(out_path)
        except OSError:
            pass

    try:
        doc = fitz.open(pdf_path)
        pat = re.compile(r'(/Artifact <</Subtype /Watermark[^>]*>>BDC.*?EMC)', re.DOTALL)
        for pno in range(len(doc)):
            for c in doc[pno].get_contents():
                stream = doc.xref_stream(c)
                if not stream:
                    continue
                text = stream.decode("latin1", "replace")
                newtext, n = pat.subn("", text)
                if n:
                    doc.update_stream(c, newtext.encode("latin1"))
        doc.save(out_path, garbage=0, deflate=True)
        doc.close()
        _CLEAN_PDF_CACHE[key] = out_path
        return out_path
    except Exception:
        # Fall back to the original (watermarked) file if cleaning fails
        return pdf_path


def _page_render_size(page_image: str):
    """Return (width, height) of a rendered page image, or None."""
    try:
        from PIL import Image
        with Image.open(page_image) as img:
            return img.size
    except Exception:
        return None


def _find_caption_lines(pdf_path: str, page_num: int) -> list:
    """Find standalone figure caption lines verbatim in the PDF text layer.

    A caption is a short text line (less than 200pt wide) that begins with a
    figure reference like 'Fig. 6.1', 'Fig. 6.1: Title', or 'Figure 2'.
    This distinguishes it from full-width body text that merely mentions a
    figure inline (e.g. 'Fig. 6.1 shows the results ...').

    Returns list of {"text": <verbatim caption>, "bbox": [x0, y0, x1, y1]}.
    """
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    caps = []
    for blk in page.get_text("dict")["blocks"]:
        if blk["type"] != 0:
            continue
        for ln in blk["lines"]:
            text = "".join(s["text"] for s in ln["spans"]).strip()
            if not text:
                continue
            x0, y0, x1, y1 = ln["bbox"]
            if re.match(r"Fig(?:ure)?\.?\s*\d+(\.\d+)?\b", text, re.I) and (x1 - x0) < 200:
                caps.append({"text": text, "bbox": [x0, y0, x1, y1]})
    doc.close()
    # Dedupe overlapping caption lines: some PDFs repeat the same caption 2-5x
    # at nearly identical positions (text-layer artifact), which would otherwise
    # emit the same figure region multiple times. Keep the first (top-most).
    caps.sort(key=lambda c: c["bbox"][1])
    deduped = []
    for c in caps:
        dup = False
        for k in deduped:
            a, b = c["bbox"], k["bbox"]
            iw = max(0, min(a[2], b[2]) - max(a[0], b[0]))
            ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
            inter = iw * ih
            if inter > 0 and inter / max(1, (a[2] - a[0]) * (a[3] - a[1])) > 0.7:
                dup = True
                break
        if not dup:
            deduped.append(c)
    return deduped


def _background_image_sizes(pdf_path: str) -> set:
    """Return native (w, h) of images that appear on >=90% of pages.

    These are page backgrounds / watermarks (identical full-page or repeated
    artwork) and must never be treated as figure content.
    """
    import fitz
    from collections import Counter
    doc = fitz.open(pdf_path)
    counts = Counter()
    n = len(doc)
    for pno in range(n):
        page = doc[pno]
        for im in page.get_images(full=True):
            pix = fitz.Pixmap(doc, im[0])
            counts[(pix.width, pix.height)] += 1
    doc.close()
    return {k for k, c in counts.items() if c >= max(2, int(n * 0.9))}


def _content_image_rects(pdf_path: str, page_num: int, bg_sizes: set) -> list:
    """Return page-rect of embedded raster figures on a page (points coords).

    Excludes page backgrounds/watermarks (from `bg_sizes`) and tiny images
    (<100px) such as QR codes, icons and inline decorations.
    """
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    rects = []
    for im in page.get_images(full=True):
        pix = fitz.Pixmap(doc, im[0])
        if (pix.width, pix.height) in bg_sizes:
            continue
        if pix.width < 100 or pix.height < 100:
            continue
        for r in page.get_image_rects(im[0]):
            rects.append([r.x0, r.y0, r.x1, r.y1])
    doc.close()
    return rects


def _real_raster_regions(pdf_path: str, page_num: int, bg_sizes: set,
                         min_px: int = 40) -> list:
    """Return genuine embedded raster figures on a page.

    Each entry: {xref, rect_pt:[x0,y0,x1,y1], w, h}. Excludes page
    backgrounds/watermarks (`bg_sizes`) and tiny noise (<min_px native).
    This is the authoritative source for the no-caption fallback: a page
    with no such figures has no diagrams — Gemma must never invent regions
    on pure-text pages (it crops text and activity boxes as 'diagrams').
    """
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    regions = []
    for im in page.get_images(full=True):
        xref = im[0]
        pix = fitz.Pixmap(doc, xref)
        if (pix.width, pix.height) in bg_sizes:
            continue
        if pix.width < min_px or pix.height < min_px:
            continue
        for r in page.get_image_rects(xref):
            regions.append({
                "xref": xref,
                "rect_pt": [r.x0, r.y0, r.x1, r.y1],
                "w": pix.width,
                "h": pix.height,
            })
    doc.close()
    return regions


def _vector_figure_regions(pdf_path: str, page_num: int, min_items: int = 40,
                           gap: int = 8) -> list:
    """Detect genuine vector-drawn figures (graphs, circuit diagrams, etc.)
    on a page with no captions and no raster figures.

    Clusters vector drawing rects into connected blobs, then applies
    decorative-art guards:
      - clusters that extend off-page (x0<0 or x1>pw) are cover/edge art
      - clusters touching the bottom margin are footer decorations
      - thin full-width rules and full-page-width bands are dividers
      - small clusters (<80x60pt) are icons/badges, not figures

    Returns a list of {rect_pt:[x0,y0,x1,y1]} in page-point coordinates.
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        pw, ph = page.rect.width, page.rect.height
        rects = []
        for d in page.get_drawings():
            r = d["rect"]
            w, h = r.width, r.height
            if w > 0.55 * pw and h > 0.55 * ph:
                continue  # full-page background
            if (r.x0 < -0.5 or r.x1 > pw + 0.5 or
                    r.y0 < -0.5 or r.y1 > ph + 0.5):
                continue  # extends off-page (page-boundary hairlines / clip art)
            # Tiny filled polygons (3-vertex gradient fills) are decorative
            # watermark/gradient noise, not figure content. They bridge figure
            # clusters to the page edges and must not anchor clusters.
            if len(d["items"]) <= 3 and d.get("fill") and (w < 200 or h < 200):
                continue
            rects.append([r.x0, r.y0, r.x1, r.y1])
        doc.close()
        if not rects:
            return []

        clusters = []
        for r in rects:
            added = False
            for c in clusters:
                if (r[0] < c[2] + gap and r[2] > c[0] - gap
                        and r[1] < c[3] + gap and r[3] > c[1] - gap):
                    c[0] = min(c[0], r[0]); c[1] = min(c[1], r[1])
                    c[2] = max(c[2], r[2]); c[3] = max(c[3], r[3]); c[4] += 1
                    added = True
                    break
            if not added:
                clusters.append(r + [1])

        # Collapse chained clusters so figures split across parts merge fully.
        changed = True
        while changed:
            changed = False
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    a, b = clusters[i], clusters[j]
                    if (a[0] < b[2] + gap and a[2] > b[0] - gap
                            and a[1] < b[3] + gap and a[3] > b[1] - gap):
                        clusters[i] = [min(a[0], b[0]), min(a[1], b[1]),
                                       max(a[2], b[2]), max(a[3], b[3]),
                                       a[4] + b[4]]
                        del clusters[j]
                        changed = True
                        break
                if changed:
                    break

        out = []
        for c in clusters:
            x0, y0, x1, y1, items = c
            w, h = x1 - x0, y1 - y0
            if items < min_items:
                continue
            if x0 < -0.5 or x1 > pw + 0.5:
                continue  # extends off-page (decorative art)
            if y1 > ph - 15:
                continue  # touches bottom margin (footer decoration)
            if h < 10 and w > 0.4 * pw:
                continue  # thin full-width rule/dividers
            if w > 0.85 * pw and h < 0.5 * ph:
                continue  # full-width band (thin decorative band, not a figure)
            if w < 80 or h < 60:
                continue  # too small to be a real figure
            out.append({"rect_pt": [x0, y0, x1, y1]})
        return out
    except Exception:
        return []


def _svg_escape(text: str) -> str:
    """Escape a string for safe embedding in SVG XML."""
    from xml.sax.saxutils import escape
    return escape(text, {'"': '&quot;'})


def _color_rgb(value, default=(0, 0, 0)):
    """Convert a PyMuPDF color (0-255 int or 0-1 float tuple) to (r,g,b)."""
    if value is None:
        return default
    if isinstance(value, (tuple, list)):
        if all(isinstance(c, float) for c in value):
            return tuple(int(round(min(1.0, max(0.0, c)) * 255)) for c in value)
        return tuple(int(c) for c in value)
    v = int(value)
    return (v >> 16 & 255, v >> 8 & 255, v & 255)


def extract_vector_figure_svg(pdf_path: str, page_num: int, bbox_pt: list,
                              out_path: str = None, pad_pt: float = 4.0,
                              dpi: float = 300.0) -> Optional[str]:
    """Export the vector artwork inside a figure bbox as a clean SVG.

    Unlike `page.get_svg_image()` (which dumps the whole page including
    embedded rasters and page backgrounds), this builds an SVG from only the
    vector drawing objects and text spans that intersect the figure bbox, in
    the figure's own coordinate space. Raster content inside the box (photos,
    full-page backgrounds) is deliberately dropped — those figures are handled
    separately as native embedded PNGs by the main pipeline.

    Used for vector-native figures (graphs, circuit diagrams, schematics) so
    they can be animated / recolored / scaled without pixelation in the final
    video. Returns the SVG string, or None if no vector content is present.
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        fig = fitz.Rect(bbox_pt)
        if fig.is_empty or fig.width < 10 or fig.height < 10:
            doc.close()
            return None
        fig = fig + (-pad_pt, -pad_pt, pad_pt, pad_pt)
        x0, y0 = fig.x0, fig.y0

        def fmt_pt(p):
            return f"{p.x - x0:.2f},{p.y - y0:.2f}"

        def path_for(draw):
            d = []
            started = False
            for it in draw["items"]:
                op = it[0]
                if op == "l":
                    p1, p2 = it[1], it[2]
                    if not started:
                        d.append(f"M {fmt_pt(p1)}")
                        started = True
                    d.append(f"L {fmt_pt(p2)}")
                elif op == "re":
                    r = it[1]
                    d.append(f"M {r.x0 - x0:.2f},{r.y0 - y0:.2f} "
                             f"h {r.width:.2f} v {r.height:.2f} h {-r.width:.2f} Z")
                elif op == "c":
                    p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
                    if not started:
                        d.append(f"M {fmt_pt(p1)}")
                        started = True
                    d.append(f"C {fmt_pt(p2)} {fmt_pt(p3)} {fmt_pt(p4)}")
                elif op == "qu":
                    q = it[1]  # Quad: 4 corner points
                    pts = [q.ul, q.ur, q.lr, q.ll]
                    d.append(f"M {fmt_pt(pts[0])}")
                    for p in pts[1:]:
                        d.append(f"L {fmt_pt(p)}")
                    d.append("Z")
            return " ".join(d)

        paths = []
        for draw in page.get_drawings():
            r = draw["rect"]
            if not (r.x1 < fig.x0 or r.x0 > fig.x1 or r.y1 < fig.y0 or r.y0 > fig.y1):
                d = path_for(draw)
                if d:
                    paths.append((d, draw))

        # Only keep short text labels inside the figure. Full-width lines that
        # merely overlap the box edge are body text, not figure labels.
        texts = []
        for blk in page.get_text("dict")["blocks"]:
            if blk["type"] != 0:
                continue
            for ln in blk["lines"]:
                line_w = ln["bbox"][2] - ln["bbox"][0]
                if line_w > fig.width * 0.8:
                    continue
                for sp in ln["spans"]:
                    b = fitz.Rect(sp["bbox"])
                    if fig.contains(b) or fig.intersects(b):
                        color = sp.get("color", 0)
                        texts.append((sp["text"], sp["bbox"], sp["size"], _color_rgb(color)))
        doc.close()

        if not paths and not texts:
            return None

        scale = 1.0
        if dpi and dpi != 72.0:
            scale = dpi / 72.0
        W, H = fig.width * scale, fig.height * scale

        def color_str(rgb):
            return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"

        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'width="{W:.1f}" height="{H:.1f}" viewBox="0 0 {W:.1f} {H:.1f}">']
        parts.append(f'<g transform="scale({scale:.3f})">')
        for d, draw in paths:
            fill = draw.get("fill")
            stroke = draw.get("color")
            sw = draw.get("width") or 1.0
            if fill is not None:
                fr = _color_rgb(fill)
                parts.append(f'<path d="{d}" fill="{color_str(fr)}" '
                             f'fill-opacity="{draw.get("fill_opacity", 1):.2f}"/>')
            else:
                sr = _color_rgb(stroke)
                parts.append(f'<path d="{d}" fill="none" stroke="{color_str(sr)}" '
                             f'stroke-width="{sw:.2f}"/>')
        for txt, bbox, size, rgb in texts:
            tx = bbox[0] - x0
            ty = bbox[1] - y0 + size * 0.8
            parts.append(f'<text x="{tx:.2f}" y="{ty:.2f}" font-size="{size:.2f}" '
                         f'fill="{color_str(rgb)}">{_svg_escape(txt)}</text>')
        parts.append("</g>")
        parts.append("</svg>")

        svg = "\n".join(parts)
        if out_path:
            with open(out_path, "w") as f:
                f.write(svg)
        return svg
    except Exception:
        return None


def _ink_figure_bbox(page_image: str, caption_top_pt: float, band_top_pt: float = 62,
                     dpi: int = 150) -> Optional[list]:
    """Locate the figure artwork directly above a caption using ink analysis.

    Renders the page, thresholds to ink, dilates to merge vector lines into one
    blob, then finds the connected component nearest the caption bottom that is
    large enough to be a real figure. Returns pixel bbox [x1,y1,x2,y2] or None.

    `band_top_pt` bounds the search above: for pages with multiple figures this
    is the bottom of the previous figure's caption, so each figure is isolated
    in its own band instead of merging with the one above it.
    """
    import cv2
    import numpy as np
    try:
        img = cv2.imread(page_image)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, binimg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        binimg = cv2.dilate(binimg, np.ones((3, 3), np.uint8), iterations=2)
        sc = dpi / 72.0
        y_bottom = int((caption_top_pt - 2) * sc)
        y_top = max(int(50 * sc), int(band_top_pt * sc))  # below header / above prev caption
        if y_bottom <= y_top:
            return None
        region = binimg[y_top:y_bottom, :]
        ncc, _, stats, _ = cv2.connectedComponentsWithStats(region, 8)
        comps = []
        for i in range(1, ncc):
            x, y, w, h, area = stats[i]
            if w < 40 or h < 20 or area < 300:
                continue
            proximity = y_bottom - (y + h)  # smaller = closer to the caption
            comps.append((x, y, w, h, area, proximity))
        if not comps:
            return None

        # The figure is the component whose bottom edge is nearest the caption
        # AND that is large enough to be a real figure (not a thin label strip).
        near = [c for c in comps if c[5] < int(40 * dpi / 72)]
        pool = near if near else comps
        x, y, w, h, area, _ = max(pool, key=lambda c: c[4])
        rx0, ry0, rx1, ry1 = x, y_top + y, x + w, y_top + y + h

        # Union any components vertically adjacent (within a small gap) and
        # horizontally overlapping the chosen figure, so split vector artwork
        # (e.g. axis lines separated from the body) stays inside the crop.
        changed = True
        while changed:
            changed = False
            for (x2, y2, w2, h2, a2, _) in comps:
                cy0 = y_top + y2
                cy1 = y_top + y2 + h2
                if cy0 <= ry1 + int(12 * dpi / 72) and cy1 >= ry0 - int(12 * dpi / 72):
                    if x2 < rx1 and x2 + w2 > rx0:
                        nx0, ny0 = min(rx0, x2), min(ry0, cy0)
                        nx1, ny1 = max(rx1, x2 + w2), max(ry1, cy1)
                        if (nx1 - nx0) * (ny1 - ny0) > (rx1 - rx0) * (ry1 - ry0):
                            rx0, ry0, rx1, ry1 = nx0, ny0, nx1, ny1
                            changed = True

        pad = 4
        return [max(0, rx0 - pad), max(0, ry0 - pad),
                min(img.shape[1], rx1 + pad), min(img.shape[0], ry1 + pad)]
    except Exception:
        return None


def _body_text_bottom_above(pdf_path: str, page_num: int, caption_top_pt: float) -> float:
    """Bottom (pt) of the last full-width body text line above a caption.

    Figures sit between the paragraph that mentions them and their caption.
    Body lines are full-width (start near the left margin and span most of the
    column); figure labels (axis ticks, point names) are short and centered, so
    they never qualify. Returns the header baseline (~62pt) if nothing found.
    """
    import fitz
    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        best = 62.0
        for blk in page.get_text("dict")["blocks"]:
            if blk["type"] != 0:
                continue
            for ln in blk["lines"]:
                text = "".join(s["text"] for s in ln["spans"]).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = ln["bbox"]
                # Full-width body line: starts at/near the left margin and spans
                # most of the column. Figure labels never satisfy both.
                if x0 < 110 and (x1 - x0) > 280 and y1 <= caption_top_pt + 2:
                    best = max(best, y1)
        doc.close()
        return best
    except Exception:
        return 62.0


def _vector_region_for_caption(pdf_path: str, page_num: int, caption_bbox: list,
                               band_top_pt: float = 62) -> Optional[list]:
    """Best vector-drawn figure region directly above a caption, or None.

    Picks the vector-drawing cluster whose bottom edge sits just above the
    caption (or reaches it) and which overlaps the caption's horizontal span.
    Used as a fallback when ink analysis returns nothing or only a degenerate
    strip (e.g. a vertical axis line) — and as ground truth for figures that
    sit *beside* body text, where the ink band degenerates.
    """
    try:
        regions = _vector_figure_regions(pdf_path, page_num)
        if not regions:
            return None
        cx0, cy0, cx1, cy1 = caption_bbox
        best = None
        best_area = 0.0
        for r in regions:
            x0, y0, x1, y1 = r["rect_pt"]
            # bottom must not extend far past the caption top; must start above
            # the band top (or within a small tolerance for side-figures)
            if y1 > cy0 + 15:
                continue
            if y0 > cy0 - 2:
                continue
            # horizontal overlap with the caption's span
            if x1 < cx0 or x0 > cx1:
                continue
            area = (x1 - x0) * (y1 - y0)
            if area > best_area:
                best_area = area
                best = [x0, y0, x1, y1]
        return best
    except Exception:
        return None


def _vector_region_below_caption(pdf_path: str, page_num: int,
                                 caption_bbox: list) -> Optional[list]:
    """Best vector-drawn figure region directly BELOW a caption (title-above-
    figure layout), or None.

    Some figures carry their caption as a title printed ABOVE the artwork
    (common for anatomy plates). The vector cluster whose top edge sits just
    below the caption and which overlaps the caption's horizontal span is that
    figure. Returns [x0, y0, x1, y1] in points or None.
    """
    try:
        regions = _vector_figure_regions(pdf_path, page_num)
        if not regions:
            return None
        cx0, cy0, cx1, cy1 = caption_bbox
        best = None
        best_area = 0.0
        for r in regions:
            x0, y0, x1, y1 = r["rect_pt"]
            if y0 < cy1 - 30:
                continue  # starts well above the caption — a figure above it
            if y1 < cy0:
                continue
            if x1 < cx0 or x0 > cx1:
                continue  # no horizontal overlap with the caption's span
            area = (x1 - x0) * (y1 - y0)
            if area > best_area:
                best_area = area
                best = [x0, y0, x1, y1]
        return best
    except Exception:
        return None


def _looks_like_caption_text_strip(bbox_pt: list, caption_top_pt: float,
                                   caption_bottom_pt: float,
                                   pdf_path: str = None,
                                   page_num: int = None) -> bool:
    """True when an ink bbox is really the caption's own letters (caption text
    set vertically beside a figure), not artwork. A caption-text strip hugs the
    caption line and is NOT backed by any vector-drawing cluster, while genuine
    vector artwork always produces a drawing cluster inside its bbox."""
    if not bbox_pt:
        return True
    w = bbox_pt[2] - bbox_pt[0]
    h = bbox_pt[3] - bbox_pt[1]
    if w <= 0 or h <= 0:
        return True
    # Anything hugging the caption within a small gap and whose bbox contains no
    # vector figure cluster is its own text line or a vertical letter column.
    if pdf_path is not None and page_num is not None:
        gap = caption_top_pt - bbox_pt[3]
        if 0 <= gap < 20:
            for r in _vector_figure_regions(pdf_path, page_num):
                rx0, ry0, rx1, ry1 = r["rect_pt"]
                inter_w = max(0, min(bbox_pt[2], rx1) - max(bbox_pt[0], rx0))
                inter_h = max(0, min(bbox_pt[3], ry1) - max(bbox_pt[1], ry0))
                if inter_w > 0 and inter_h > 0:
                    inter = inter_w * inter_h
                    if inter > 0.3 * w * h:
                        return False  # a real vector cluster backs it
            return True  # no vector artwork inside — caption text
    return False


def _is_degenerate_figure_bbox(bbox_pt: list) -> bool:
    """True when a figure bbox is a thin strip (e.g. a lone axis line) rather
    than a real figure: very narrow width relative to height, or a single line
    with almost no area."""
    if not bbox_pt:
        return True
    w = bbox_pt[2] - bbox_pt[0]
    h = bbox_pt[3] - bbox_pt[1]
    if w <= 0 or h <= 0:
        return True
    if h > 100 and w / h < 0.25:
        return True
    return False


def _figure_bbox_for_caption(pdf_path: str, page_num: int, caption_bbox: list,
                             content_rects: list, page_image: str,
                             band_top_pt: float = 62, dpi: int = 150) -> Optional[list]:
    """Compute the pixel bbox of the figure belonging to one caption line.

    Strategy:
      1. If an embedded raster figure sits directly above the caption and
         overlaps its horizontal span, use that exact image rect (ground truth).
      2. Otherwise (vector-drawn figure) use ink/connected-component analysis
         within the band bounded above by `band_top_pt` (previous caption or
         the page header) and below by the caption.
      3. If ink analysis returns nothing or only a thin degenerate strip,
         fall back to vector-drawing clusters above the caption (handles
         figures that sit beside body text and two-panel figures whose axis
         line is the biggest ink component).

    Returns pixel bbox [x1, y1, x2, y2] or None.
    """
    cx0, cy0, cx1, cy1 = caption_bbox  # points coords
    for r in content_rects:
        if r[1] < cy0 and r[3] <= cy0 + 5 and r[0] < cx1 and r[2] > cx0:
            sc = dpi / 72.0
            pad = 4
            return [max(0, int(r[0] * sc) - pad), max(0, int(r[1] * sc) - pad),
                    int(r[2] * sc) + pad, int(min(r[3], cy0) * sc) + pad]

    ink = _ink_figure_bbox(page_image, cy0, band_top_pt, dpi)
    ink_pt = [v / dpi * 72.0 for v in ink] if ink else None
    # Ink gave nothing or a degenerate strip — trust vector geometry instead.
    vec_pt = _vector_region_for_caption(pdf_path, page_num, caption_bbox, band_top_pt)
    # Some figures carry their caption as a *title above the artwork* (common
    # for anatomy plates). In that layout the artwork is directly BELOW the
    # caption, which the above-caption searches can never reach. Prefer that
    # figure whenever the above-caption result is missing, degenerate, or a
    # thin vertical text strip (a caption whose ink is just its own letters).
    below_pt = _vector_region_below_caption(pdf_path, page_num, caption_bbox)
    if below_pt:
        above_ok = bool(
            vec_pt
            or (ink_pt and not _is_degenerate_figure_bbox(ink_pt)))
        if not above_ok or (
                vec_pt is None and ink_pt
                and _looks_like_caption_text_strip(ink_pt, cy0, cy1,
                                                   pdf_path, page_num)):
            sc = dpi / 72.0
            pad = 4
            return [max(0, int(below_pt[0] * sc) - pad),
                    max(0, int(below_pt[1] * sc) - pad),
                    int(below_pt[2] * sc) + pad, int(below_pt[3] * sc) + pad]

    if vec_pt:
        sc = dpi / 72.0
        pad = 4
        return [max(0, int(vec_pt[0] * sc) - pad), max(0, int(vec_pt[1] * sc) - pad),
                int(vec_pt[2] * sc) + pad, int(vec_pt[3] * sc) + pad]
    if ink_pt and not _is_degenerate_figure_bbox(ink_pt):
        return ink
    # Last resort: keep the ink result even when degenerate (a tall thin
    # figure crop still retains the artwork) — never drop the figure entirely.
    return ink


def _embedded_figure_xref(pdf_path: str, page_num: int, caption_bbox: list,
                          bg_sizes: set) -> Optional[int]:
    """Return the xref of an embedded raster figure sitting directly above a
    caption line, or None.

    Unlike a page render (which bakes the watermark overlay in), the embedded
    image binary is the original textbook artwork — extracting it natively
    gives a clean, full-resolution figure with no watermark.
    """
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    cx0, cy0, cx1, cy1 = caption_bbox
    best = None
    best_area = 0
    for im in page.get_images(full=True):
        xref = im[0]
        pix = fitz.Pixmap(doc, xref)
        if (pix.width, pix.height) in bg_sizes:
            continue
        if pix.width < 100 or pix.height < 100:
            continue
        for r in page.get_image_rects(xref):
            if r.y0 < cy0 and r.y1 <= cy0 + 5 and r.x0 < cx1 and r.x1 > cx0:
                area = (r.x1 - r.x0) * (r.y1 - r.y0)
                if area > best_area:
                    best_area = area
                    best = xref
    doc.close()
    return best


def _extract_native_embedded_image(pdf_path: str, page_num: int, xref: int,
                                   out_path: str) -> Optional[str]:
    """Extract an embedded raster figure directly from the PDF binary (via xref)
    as a clean PNG. Returns out_path or None. The embedded image is the original
    artwork — no watermark, full native resolution.
    """
    import fitz
    try:
        doc = fitz.open(pdf_path)
        base = doc.extract_image(xref)
        img_bytes = base["image"]
        ext = base["ext"]
        doc.close()

        from PIL import Image
        import io
        raw = Image.open(io.BytesIO(img_bytes))
        # jpx / cmyk / ycbcr can't be saved as PNG directly — convert to RGB(A)
        if raw.mode in ("CMYK", "YCbCr"):
            raw = raw.convert("RGB")
        elif raw.mode not in ("RGB", "RGBA"):
            raw = raw.convert("RGB")
        raw.save(out_path, "PNG")
        return out_path
    except Exception:
        return None


def _remove_paper_background(image_path: str, out_path: str) -> str:
    """Remove the white paper background from a diagram using rembg.

    Produces a transparent-background PNG containing only the figure artwork.
    On failure (or if rembg empties the image) falls back to the input and
    returns the original path.
    """
    try:
        from rembg import remove as _rembg_remove
        from PIL import Image
        import numpy as np

        img = Image.open(image_path).convert("RGB")
        out = _rembg_remove(img, post_process_mask=True)
        arr = np.array(out)
        alpha = arr[:, :, 3]
        # rembg can occasionally drop nearly everything for very thin line art.
        # Keep the crop if more than 2% of pixels survive (>= a real diagram).
        if (alpha > 0).mean() < 0.02:
            return image_path
        out.save(out_path, "PNG")
        return out_path
    except Exception:
        return image_path


def _region_area(bbox) -> float:
    """Area of a fractional bbox (0-1000 scale); 0 if invalid/missing."""
    if not bbox or len(bbox) != 4:
        return 0
    try:
        return max(0, float(bbox[2]) - float(bbox[0])) * max(0, float(bbox[3]) - float(bbox[1]))
    except (TypeError, ValueError):
        return 0


def _bbox_overlap_fraction(bbox, ref) -> float:
    """Fraction of `bbox` overlapping `ref` (both 0-1000 fractional)."""
    if not bbox or not ref or len(bbox) != 4 or len(ref) != 4:
        return 0.0
    ix0 = max(bbox[0], ref[0]); ix1 = min(bbox[2], ref[2])
    iy0 = max(bbox[1], ref[1]); iy1 = min(bbox[3], ref[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area = _region_area(bbox)
    return inter / area if area > 0 else 0.0


def _bbox_center(bbox) -> tuple:
    """Center of a fractional bbox as (cx, cy)."""
    if not bbox or len(bbox) != 4:
        return (0.0, 0.0)
    try:
        return ((float(bbox[0]) + float(bbox[2])) / 2, (float(bbox[1]) + float(bbox[3])) / 2)
    except (TypeError, ValueError):
        return (0.0, 0.0)


def _match_region_description(gemma_regions: list, ref_bbox: list,
                              used: set = None) -> str:
    """Pick the Gemma region description whose center is nearest the anchored
    bbox, consuming it so each description is used at most once. Gemma's
    bboxes for small figures are often loose/wrong, so match by proximity
    (page diagonal ~1414 on the 0-1000 scale) with a generous threshold."""
    if not gemma_regions:
        return ""
    cx, cy = _bbox_center(ref_bbox)
    best_i, best_desc, best_dist = None, "", float("inf")
    for i, g in enumerate(gemma_regions):
        if not isinstance(g, dict) or (used is not None and i in used):
            continue
        gx, gy = _bbox_center(g.get("bbox"))
        dist = ((gx - cx) ** 2 + (gy - cy) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best_i, best_desc = i, (g.get("description") or g.get("title") or "").strip()
    # Only trust the match if it's within ~30% of the page diagonal.
    if best_dist > 425 or not best_desc:
        return ""
    if used is not None:
        used.add(best_i)
    return best_desc


def _page_nearby_text(pdf_path: str, page_num: int, ref_bbox: list,
                      max_chars: int = 700) -> str:
    """Return the text blocks from a page near the anchored fractional bbox."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        pw, ph = page.rect.width, page.rect.height
        cx = (ref_bbox[0] + ref_bbox[2]) / 2 / 1000 * pw
        cy = (ref_bbox[1] + ref_bbox[3]) / 2 / 1000 * ph
        radius = 160
        parts = []
        for blk in page.get_text("blocks"):
            b = blk[:4]
            bx = (b[0] + b[2]) / 2; by = (b[1] + b[3]) / 2
            if abs(bx - cx) < radius and abs(by - cy) < radius:
                txt = blk[4].strip().replace("\n", " ")
                if txt:
                    parts.append(txt)
        doc.close()
        joined = " ".join(parts)
        return joined[:max_chars]
    except Exception:
        return ""


def _nearby_text_description(pdf_path: str, page_num: int, bbox: list,
                             max_chars: int = 300) -> str:
    """Deterministic description derived from the page text nearest a region.

    Used as a fallback when vision labeling fails so that anchored regions
    (e.g. tiny icons) are never dropped for lack of a description. Produces a
    description like 'Illustration near text: <nearby>...' using the closest
    body text — no vision call, so it is stable across runs.
    """
    txt = _page_nearby_text(pdf_path, page_num, bbox, max_chars=max_chars)
    txt = " ".join(txt.split())
    if len(txt) < 8:
        return ""
    return f"Illustration associated with the nearby text: {txt}"


def _describe_region_crop(pdf_path: str, page_num: int, region: dict,
                          full_page_path: str) -> str:
    """Crop the anchored region from the (clean) page render and ask Gemma
    to describe just that figure, giving it the nearby page text as context
    so tiny icons get grounded descriptions. Returns description text or ''."""
    try:
        bbox = region.get("bbox")
        if not bbox or len(bbox) != 4:
            return ""
        from PIL import Image
        img = Image.open(full_page_path)
        pw, ph = img.size
        x1 = int(bbox[0] / 1000 * pw); y1 = int(bbox[1] / 1000 * ph)
        x2 = int(bbox[2] / 1000 * pw); y2 = int(bbox[3] / 1000 * ph)
        if x2 <= x1 or y2 <= y1 or (x2 - x1) < 20 or (y2 - y1) < 20:
            return ""
        crop = img.crop((x1, y1, x2, y2))
        # Upscale tiny icons so Gemma can actually see the artwork.
        if crop.width < 300 or crop.height < 300:
            scale = max(1, int(max(300 / crop.width, 300 / crop.height)))
            crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
        out_dir = tempfile.mkdtemp(prefix="pdf-diagram-region-")
        crop_path = os.path.join(out_dir, "region.png")
        crop.save(crop_path)
        nearby = _page_nearby_text(pdf_path, page_num, bbox)
        desc = ""
        if nearby:
            # Grounded: the nearby page text IS the figure's context. Ask Gemma
            # for a tight one-sentence description tying artwork to that text.
            desc = _gemma_concisely_describe(crop_path, nearby)
        if not desc:
            try:
                from pipeline.lib.vision_processor import analyze_page as _analyze
                r = _analyze(crop_path, task="diagram")
            except Exception:
                r = _gemma_analyze_image(crop_path, task="diagram")
            if isinstance(r, dict):
                if r.get("analysis"):
                    desc = r["analysis"].strip()
                else:
                    diags = r.get("diagrams") or []
                    if diags and isinstance(diags[0], dict):
                        desc = (diags[0].get("description") or diags[0].get("title") or "").strip()
        try:
            os.remove(crop_path); os.rmdir(out_dir)
        except OSError:
            pass
        return desc
    except Exception:
        return ""


def _gemma_concisely_describe(image_path: str, nearby_text: str) -> str:
    """Vision call tuned for tiny figures: concise one-sentence description
    grounded in the nearby page text. max_tokens is generous because the
    model spends many tokens on reasoning before the final answer."""
    import requests
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    prompt = (
        "You are describing a single small illustration from a health textbook. "
        "The illustration sits next to this page text:\n"
        f"\"{nearby_text}\"\n"
        "Describe ONLY this illustration, in ONE short sentence (20-35 words). "
        "Use the nearby text to ground what the artwork depicts. "
        "Reply with just the sentence, no preamble."
    )
    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "max_tokens": 600,
        "temperature": 0.1,
    }
    try:
        resp = requests.post(f"{LLAMA_HOST}/v1/chat/completions", json=payload, timeout=180)
        if resp.status_code != 200:
            return ""
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def _page_layout_text(pdf_path: str, page_num: int, max_chars: int = 3000) -> str:
    """Extract text blocks with their positions to give Gemma layout context.
    Returns a compact layout summary like 'text at (0.1,0.2)-size ...' so the
    model can reason about figure placement relative to the text.
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        pw, ph = page.rect.width, page.rect.height
        blocks = page.get_text("dict")["blocks"]
        lines = []
        for blk in blocks:
            if blk["type"] != 0:  # 0 = text block
                continue
            for ln in blk["lines"]:
                x0 = blk["bbox"][0] / pw
                y0 = blk["bbox"][1] / ph
                x1 = blk["bbox"][2] / pw
                y1 = blk["bbox"][3] / ph
                text = " ".join(s["text"] for s in ln["spans"])[:80]
                if text.strip():
                    lines.append(f"  text@({x0:.2f},{y0:.2f})-({x1:.2f},{y1:.2f}): {text}")
                if len("".join(lines)) > max_chars:
                    break
            if len("".join(lines)) > max_chars:
                break
        doc.close()
        return "\n".join(lines)
    except Exception:
        return ""


def _gemma_analyze_image_layout(image_path: str, page_text: str = "") -> dict:
    """Send page image + extracted text layout to Gemma for diagram detection.
    Uses E4B's document understanding: image + positional text layout so the
    model can isolate figures with awareness of surrounding composition."""
    import requests
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    prompt = (
        "You are a document-layout expert analyzing a textbook page. "
        "Return ONLY valid JSON (no other text).\n"
        "Detect every diagram, figure, chart, table, graph, or illustration on this page. "
        "For each return:\n"
        "- 'diagram_id': short unique id like 'fig_1', 'fig_2'\n"
        "- 'label': figure label if visible (e.g., 'Fig. 6.1') or ''\n"
        "- 'title': title or caption text of the figure\n"
        "- 'description': 1-2 sentence description of what the diagram shows\n"
        "- 'bbox': tight bounding box [x1,y1,x2,y2] in fractional 0-1000 coords that "
        "encloses ONLY the diagram artwork (not caption, not surrounding text)\n"
        "- 'layout_composition': one short sentence on where it sits relative to text "
        "(e.g., 'top-right, above its caption, spans one column')\n"
        "- 'surrounding_text_context': brief 1-sentence summary of the paragraph or "
        "caption that references this figure, useful for later script narration\n"
        "Use the page's text-layout below to understand composition: figures typically sit "
        "between text blocks, above their caption, separated by whitespace or rule lines.\n"
        "EXCLUDE: headers/footers, page numbers, logos, QR codes, pure text.\n\n"
        f"PAGE TEXT LAYOUT (approximate positions):\n{page_text}\n\n"
        'If no diagrams: {"diagrams": []}'
    )

    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "max_tokens": 4096,
        "temperature": 0.1,
    }
    try:
        resp = requests.post(f"{LLAMA_HOST}/v1/chat/completions", json=payload, timeout=180)
        if resp.status_code != 200:
            return {"error": f"API {resp.status_code}: {resp.text[:200]}"}
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'\{.*"diagrams".*\}', content, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
        return {"analysis": content}
    except Exception as e:
        return {"error": str(e)}


def extract_diagrams(pdf_path: str, pdf_id: int = 0, pages: list = None,
                     store_dir: str = None, gemma_analysis: bool = True,
                     use_layout: bool = True, refine_opencv: bool = True,
                     vector_figures: bool = False,
                     svg_output: bool = False) -> list:
    """Full extraction pipeline for one PDF.

    Uses Gemma vision to detect diagrams with bounding boxes, then crops
    the actual diagram regions from the full page render. Skips full-page
    renders that contain no diagrams. Filters out QR codes and logos.

    Returns:
        list of dicts with keys: pdf_id, page_number, image_path,
          full_page_path, description, bbox, width, height, methods, status
    """
    import fitz
    from PIL import Image

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    _page_rect = doc[0].rect
    page_w_pt, page_h_pt = _page_rect.width, _page_rect.height
    doc.close()

    if pages is None:
        pages = list(range(total_pages))

    if store_dir is None:
        store_dir = str(EXTRACT_DIR / str(pdf_id))
    os.makedirs(store_dir, exist_ok=True)

    # Native sizes of images that repeat on nearly every page (backgrounds,
    # watermarks, repeated artwork) — never treated as figures.
    bg_sizes = _background_image_sizes(pdf_path)

    all_diagrams = []

    for page_num in pages:
        page_dir = os.path.join(store_dir, f"page_{page_num}")
        os.makedirs(page_dir, exist_ok=True)

        full_page_path = _get_pdf_page_image(pdf_path, page_num, dpi=150)
        if not full_page_path:
            continue

        # Step 1: Extract embedded images (QR codes, logos, inserted images)
        embedded = _extract_embedded_images(pdf_path, page_num, page_dir)

        # Step 1b: Deterministic caption-anchored detection (primary).
        # When standalone 'Fig X.Y' captions exist on the page, they are the
        # authoritative source for figure regions AND verbatim labels — no Gemma
        # guesswork. Embedded raster figures or vector-ink analysis provide the
        # exact artwork bbox; the caption text is used as-is as the label.
        captions = _find_caption_lines(pdf_path, page_num)
        diagram_regions = []  # [{label, title, description, bbox, caption_bbox}]
        if captions:
            content_rects = _content_image_rects(pdf_path, page_num, bg_sizes)
            # Each figure sits between the paragraph that mentions it and its
            # caption. Band top = bottom of the last body line above, bounded by
            # the previous caption's bottom when figures are stacked (so a lower
            # caption never swallows the figure above it). Captions must be
            # processed top-to-bottom for the stack bound to work.
            band_top = 62.0
            prev_caption_bottom = 62.0
            for cap in sorted(captions, key=lambda c: c["bbox"][1]):
                body_bottom = _body_text_bottom_above(pdf_path, page_num, cap["bbox"][1])
                band_top = max(prev_caption_bottom, body_bottom)
                bbox_px = _figure_bbox_for_caption(
                    pdf_path, page_num, cap["bbox"], content_rects, full_page_path,
                    band_top_pt=band_top, dpi=150)
                if not bbox_px:
                    prev_caption_bottom = max(prev_caption_bottom, cap["bbox"][3])
                    continue
                # Preferred source: the embedded raster image directly above the
                # caption. It is the original artwork — clean and watermark-free.
                emb_xref = _embedded_figure_xref(pdf_path, page_num, cap["bbox"], bg_sizes)
                pw_, ph_ = _page_render_size(full_page_path)
                if not pw_:
                    continue
                # Convert pixel bbox to fractional 0-1000 coords (existing crop path)
                bbox_frac = [bbox_px[0] / pw_ * 1000, bbox_px[1] / ph_ * 1000,
                             bbox_px[2] / pw_ * 1000, bbox_px[3] / ph_ * 1000]
                cap_text = cap["text"]
                # Split verbatim caption into label + optional title ('Fig. 1.1: Title')
                m = re.match(r"^(Fig(?:ure)?\.?\s*\d+(?:\.\d+)?)\s*[:.\u2013\u2014]?\s*(.*)$",
                             cap_text, re.I | re.S)
                label = (m.group(1).strip() if m else cap_text) or cap_text
                title = (m.group(2).strip() if m and m.group(2).strip() else "")
                diagram_regions.append({
                    "diagram_id": f"fig_{len(diagram_regions) + 1}",
                    "label": label,
                    "title": title,
                    "description": cap_text,
                    "bbox": bbox_frac,
                    "caption_bbox": cap["bbox"],
                    "layout_composition": "caption-anchored (figure directly above its caption)",
                    "surrounding_text_context": cap_text,
                    "_no_refine": True,  # bbox already computed from artwork, don't re-tighten
                    "_emb_xref": emb_xref,
                })
                prev_caption_bottom = max(prev_caption_bottom, cap["bbox"][3])
        elif gemma_analysis:
            # Step 2: No-caption fallback. A page may only have diagrams if it
            # actually contains real visual content (embedded raster figures).
            # Anchor each region to a real image rect; Gemma only describes the
            # figure, it never invents a bbox from text/activity boxes.
            real_figures = _real_raster_regions(pdf_path, page_num, bg_sizes)
            diagram_regions = []
            if real_figures:
                # page size in points for fractional coord conversion
                import fitz as _fz
                _d = _fz.open(pdf_path)
                _pw, _ph = _d[page_num].rect.width, _d[page_num].rect.height
                _d.close()
                for fig in real_figures:
                    x0, y0, x1, y1 = fig["rect_pt"]
                    bbox_frac = [x0 / _pw * 1000, y0 / _ph * 1000,
                                 x1 / _pw * 1000, y1 / _ph * 1000]
                    diagram_regions.append({
                        "label": "",
                        "title": "",
                        "description": "",  # filled by vision labeling below
                        "bbox": bbox_frac,
                        "_no_refine": True,  # exact embedded-image rect
                        "_emb_xref": fig["xref"],
                    })
            if not diagram_regions and vector_figures:
                # Optional vector-drawn figure detection (opt-in). Off by
                # default: corpus sweeps show text-as-vector pages (Sanskrit/
                # Urdu/Hindi) and math/activity callouts cluster like figures,
                # producing widespread false positives.
                vec_figs = _vector_figure_regions(pdf_path, page_num)
                if vec_figs:
                    import fitz as _fz
                    _d = _fz.open(pdf_path)
                    _pw, _ph = _d[page_num].rect.width, _d[page_num].rect.height
                    _d.close()
                    for fig in vec_figs:
                        x0, y0, x1, y1 = fig["rect_pt"]
                        bbox_frac = [x0 / _pw * 1000, y0 / _ph * 1000,
                                     x1 / _pw * 1000, y1 / _ph * 1000]
                        diagram_regions.append({
                            "label": "",
                            "title": "",
                            "description": "",
                            "bbox": bbox_frac,
                            "_no_refine": True,
                            "_emb_xref": None,
                        })
            # Descriptions: run Gemma once on the page for pages that have real
            # figures, restricted to the anchored regions. If it fails or finds
            # nothing, regions keep empty descriptions and the vision-labeling
            # step later fills them with page context.
            if diagram_regions and use_layout:
                layout_text = _page_layout_text(pdf_path, page_num)
                r = _gemma_analyze_image_layout(full_page_path, page_text=layout_text)
                if isinstance(r, dict) and "diagrams" in r:
                    by_area = sorted(r["diagrams"], key=lambda d: _region_area(d.get("bbox")), reverse=True)
                    used = set()
                    for i, region in enumerate(diagram_regions):
                        desc = _match_region_description(by_area, region["bbox"], used)
                        if desc:
                            region["description"] = desc
            if diagram_regions:
                # Fill any still-empty descriptions via vision labeling on the
                # anchored region crop (uses page context when available).
                for region in diagram_regions:
                    if region["description"]:
                        continue
                    desc = _describe_region_crop(pdf_path, page_num, region, full_page_path)
                    if desc:
                        region["description"] = desc
                    else:
                        # Deterministic fallback: never drop an anchored region
                        # just because the vision call failed / returned nothing.
                        region["description"] = _nearby_text_description(
                            pdf_path, page_num, region["bbox"])

        if not diagram_regions:
            # No diagrams on this page — skip entirely (don't save full page render)
            try:
                os.remove(full_page_path)
                os.rmdir(os.path.dirname(full_page_path))
            except OSError:
                pass
            continue

        # Step 3: Crop each diagram region from the full page render
        page_img = Image.open(full_page_path)
        pw, ph = page_img.size

        # Also keep a reference copy of the full page for context
        shutil_path = os.path.join(page_dir, "full_page.png")
        try:
            import shutil
            shutil.copy2(full_page_path, shutil_path)
        except OSError:
            pass

        for idx, region in enumerate(diagram_regions):
            bbox = region.get("bbox")
            desc_parts = []
            if region.get("_no_refine"):
                # Caption-anchored: description IS the verbatim caption text,
                # exactly as it appears in the PDF (no re-paraphrasing).
                description = region.get("description") or ""
            else:
                if region.get("label"):
                    desc_parts.append(region["label"])
                if region.get("title"):
                    desc_parts.append(region["title"])
                if region.get("description"):
                    desc_parts.append(region["description"])
                description = " — ".join(desc_parts) if desc_parts else ""

            cropped_path = None
            crop_w, crop_h = 0, 0
            crop_bbox = None
            source_method = "render_crop"

            # Preferred: native embedded raster figure (clean, watermark-free,
            # full resolution). Extract the original artwork straight from the
            # PDF binary instead of cropping the (cleaned) page render.
            emb_xref = region.get("_emb_xref") if isinstance(region, dict) else None
            if emb_xref:
                native_fname = f"diagram_p{page_num}_{idx}.png"
                native_path = os.path.join(page_dir, native_fname)
                if _extract_native_embedded_image(pdf_path, page_num, emb_xref, native_path):
                    with Image.open(native_path) as nimg:
                        crop_w, crop_h = nimg.size
                    cropped_path = native_path
                    crop_bbox = None  # native size, not a page-render crop
                    source_method = "native_embedded"
                else:
                    emb_xref = None

            if not cropped_path and bbox and len(bbox) == 4:
                # Convert fractional coords (0-1000) to pixel coords
                x1 = int(bbox[0] / 1000 * pw)
                y1 = int(bbox[1] / 1000 * ph)
                x2 = int(bbox[2] / 1000 * pw)
                y2 = int(bbox[3] / 1000 * ph)
                # Validate
                if x2 > x1 and y2 > y1 and (x2 - x1) > 50 and (y2 - y1) > 50:
                    # Optional: tighten the box to the actual artwork via OpenCV.
                    # Skipped for caption-anchored regions whose bbox already
                    # comes from the exact embedded image rect or ink analysis.
                    if refine_opencv and not region.get("_no_refine"):
                        x1, y1, x2, y2 = _refine_bbox_with_opencv(
                            full_page_path, [x1, y1, x2, y2])
                    crop = page_img.crop((x1, y1, x2, y2))
                    crop_w, crop_h = crop.size
                    crop_fname = f"diagram_p{page_num}_{idx}.png"
                    cropped_path = os.path.join(page_dir, crop_fname)
                    crop.save(cropped_path)
                    crop_bbox = json.dumps([x1, y1, x2, y2])
                    source_method = "clean_render_crop"
                else:
                    # Bbox too small or invalid — use embedded image if available
                    cropped_path = None

            # Vector-native figures: also export the artwork as a clean SVG
            # (paths + labels only, no raster background) for animated,
            # resolution-independent use in the final video. Skips regions
            # whose artwork was already captured as a native embedded raster
            # image — those stay PNG (a photo / detailed bitmap).
            svg_path = None
            if svg_output and cropped_path and emb_xref is None and bbox and len(bbox) == 4:
                # Convert fractional bbox to page-point coords (the SVG
                # exporter works in the PDF's native point space).
                pt_x0 = bbox[0] / 1000 * page_w_pt
                pt_y0 = bbox[1] / 1000 * page_h_pt
                pt_x1 = bbox[2] / 1000 * page_w_pt
                pt_y1 = bbox[3] / 1000 * page_h_pt
                svg_fname = f"diagram_p{page_num}_{idx}.svg"
                svg_path = os.path.join(page_dir, svg_fname)
                if not extract_vector_figure_svg(
                        pdf_path, page_num, [pt_x0, pt_y0, pt_x1, pt_y1], svg_path):
                    svg_path = None

            if not cropped_path:
                # Try to find a matching embedded image for this page
                matched = False
                for emb in embedded:
                    ew, eh = emb["width"], emb["height"]
                    # Skip tiny images (<100px) and QR-code-like square images
                    if ew < 100 or eh < 100:
                        continue
                    if ew > 2000 or eh > 2000:
                        continue
                    # Check if it looks like a QR code (near-square and < 300px)
                    if ew < 300 and eh < 300 and abs(ew - eh) / max(ew, eh) < 0.2:
                        continue  # skip QR codes
                    cropped_path = emb["path"]
                    crop_w, crop_h = ew, eh
                    crop_bbox = json.dumps(emb["bbox"]) if emb["bbox"] else None
                    matched = True
                    source_method = "embedded_fallback"
                    break

                if not matched:
                    # Last resort: use the full page render itself
                    cropped_path = shutil_path
                    crop_w, crop_h = pw, ph
                    crop_bbox = json.dumps([0, 0, pw, ph])
                    source_method = "full_page_render"

            # Remove the white paper background from the final artwork so the
            # diagram is a usable standalone PNG (transparent background).
            # Only applied to genuine figures, never to full-page fallbacks.
            if cropped_path and source_method != "full_page_render":
                transparent_fname = f"diagram_p{page_num}_{idx}_clear.png"
                transparent_path = os.path.join(page_dir, transparent_fname)
                final_path = _remove_paper_background(cropped_path, transparent_path)
                if final_path == transparent_path:
                    source_method += "+transparent"
                    with Image.open(final_path) as fin:
                        crop_w, crop_h = fin.size
                    cropped_path = final_path

            caption_bbox = region.get("caption_bbox") if isinstance(region, dict) else None
            meta = {
                "diagram_id": region.get("diagram_id", ""),
                "label": region.get("label", ""),
                "caption_bbox": caption_bbox,
                "layout_composition": region.get("layout_composition", ""),
                "surrounding_text_context": region.get("surrounding_text_context", ""),
            }
            diag = {
                "pdf_id": pdf_id,
                "page_number": page_num,
                "image_path": cropped_path,
                "svg_path": svg_path,
                "full_page_path": shutil_path,
                "description": description,
                "bbox": crop_bbox,
                "caption_bbox": json.dumps(caption_bbox) if caption_bbox else None,
                "width": crop_w,
                "height": crop_h,
                "methods": json.dumps({"source": source_method,
                                       "layout": bool(use_layout),
                                       "opencv": bool(refine_opencv),
                                       "svg": bool(svg_path)}),
                "metadata": json.dumps(meta),
                "status": "analyzed",
            }
            all_diagrams.append(diag)

        page_img.close()

        # Cleanup temp file
        try:
            os.remove(full_page_path)
            os.rmdir(os.path.dirname(full_page_path))
        except OSError:
            pass

    return all_diagrams


def extract_and_store(pdf_path: str, pdf_id: int, db_conn=None,
                      pages: list = None, use_layout: bool = True,
                      refine_opencv: bool = True, svg_output: bool = False) -> list:
    """Extract diagrams and store them in the database.

    Returns list of inserted diagram dicts with their DB IDs.
    Only stores diagrams that pass the intentional diagram filter
    (has numbered figure labels, substantial descriptions, etc.).
    """
    diagrams = extract_diagrams(pdf_path, pdf_id=pdf_id, pages=pages,
                                use_layout=use_layout, refine_opencv=refine_opencv,
                                vector_figures=False, svg_output=svg_output)

    # Filter to only intentional diagrams before storing
    diagrams = filter_intentional_diagrams(diagrams)

    if db_conn and diagrams:
        for d in diagrams:
            db_conn.execute(
                """INSERT INTO pdf_diagrams
                   (pdf_id, page_number, image_path, svg_path, full_page_path, description,
                    bbox, width, height, methods, metadata, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d["pdf_id"], d["page_number"], d["image_path"], d["svg_path"],
                 d["full_page_path"], d["description"], d["bbox"], d["width"],
                 d["height"], d["methods"], d["metadata"], d["status"]),
            )
        db_conn.commit()

    return diagrams


def _has_intentional_label(description: str) -> bool:
    """Check if a diagram description contains a numbered figure label,
    which indicates it's an intentional textbook diagram (not a random image)."""
    if not description:
        return False
    patterns = [
        r'\b(?:Fig|Figure|TABLE|Table|Chart|Graph|Diagram|Fig\.)\s*\.?\s*\d+(?:\.\d+)?\b',
        r'\b\d+\.\d+\s',  # numbering pattern like "6.1"
        r'\b(?:Fig|Figure|TABLE|Table|Chart|Graph)\s*[A-Z]?\d+',
    ]
    for pat in patterns:
        if re.search(pat, description, re.IGNORECASE):
            return True
    return False


def filter_intentional_diagrams(diagrams: list, min_confidence: bool = True) -> list:
    """Filter to only include diagrams with intentional labels (numbered figures).
    Also excludes diagrams where description is too vague or empty.
    Intentional textbook diagrams usually have numbered labels like 'Fig. 6.1'
    or at minimum a specific description of what the diagram teaches."""
    filtered = []
    for d in diagrams:
        desc = d.get("description", "") or ""
        if not desc.strip():
            continue
        # Explicitly skip "No diagram" declarations
        if desc.lower().startswith("no diagram") or "no diagram on this page" in desc.lower():
            continue
        # Intentional diagrams have figure labels
        if _has_intentional_label(desc):
            filtered.append(d)
            continue
        # Also keep diagrams with substantial descriptions (2+ sentences)
        if len(desc) > 80 and (desc.count(".") >= 2 or desc.count("shows") > 0):
            filtered.append(d)
            continue
        # Keep if description mentions specific diagram type (but only with some substance)
        if len(desc) > 40:
            diagram_types = ["graph", "chart", "table", "flowchart", "diagram",
                            "illustration", "drawing", "map", "anatomical",
                            "circuit", "structure", "reaction", "figure"]
            if any(t in desc.lower() for t in diagram_types):
                filtered.append(d)
    return filtered


def build_diagram_context(diagrams: list) -> str:
    """Build a text summary of all diagrams for injecting into an LLM prompt.

    One entry per page on pages where Gemma detected a diagram.
    Includes layout composition and surrounding text context when available.
    """
    parts = []
    seen_pages = set()
    for d in diagrams:
        desc = d.get("description", "").strip()
        page = d.get("page_number", 0)
        if not desc:
            continue
        if page not in seen_pages:
            seen_pages.add(page)
            meta = {}
            try:
                meta = json.loads(d.get("metadata") or "{}")
            except (TypeError, ValueError):
                meta = {}
            comp = (meta.get("layout_composition") or "").strip()
            ctx = (meta.get("surrounding_text_context") or "").strip()
            trimmed = desc[:1500].strip()
            extra = ""
            if comp:
                extra += f" (layout: {comp}"
                extra += f"; context: {ctx})" if ctx else ")"
            elif ctx:
                extra += f" (context: {ctx})"
            parts.append(f"[Textbook Diagram on page {page + 1}: {trimmed}{extra}]")
    return "\n".join(parts) if parts else ""


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1]
    pid = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    result = extract_diagrams(path, pdf_id=pid)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    ctx = build_diagram_context(result)
    print("\n--- Diagram Context ---")
    print(ctx[:2000])
