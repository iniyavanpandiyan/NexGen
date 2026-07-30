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
    """
    out_dir = tempfile.mkdtemp(prefix="pdf-diagram-")
    stem = Path(pdf_path).stem
    out_path = os.path.join(out_dir, f"{stem}_p{page_num}.png")

    try:
        subprocess.run(
            ["pdftoppm", "-png", "-f", str(page_num + 1), "-l", str(page_num + 1),
             "-r", str(dpi), "-singlefile", pdf_path, os.path.join(out_dir, f"{stem}_p{page_num}")],
            capture_output=True, timeout=60, check=True
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        import fitz
        doc = fitz.open(pdf_path)
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
            "representing the diagram region on the page\n\n"
            "Be generous — include tables, graphs, charts, anatomical drawings, circuit diagrams, "
            "chemical structures, maps, flowcharts as diagrams.\n"
            "EXCLUDE: QR codes, page headers/footers, logos, page numbers, navigation icons.\n"
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
        "max_tokens": 2048,
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


def extract_diagrams(pdf_path: str, pdf_id: int = 0, pages: list = None,
                     store_dir: str = None, gemma_analysis: bool = True) -> list:
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
    doc.close()

    if pages is None:
        pages = list(range(total_pages))

    if store_dir is None:
        store_dir = str(EXTRACT_DIR / str(pdf_id))
    os.makedirs(store_dir, exist_ok=True)

    all_diagrams = []

    for page_num in pages:
        page_dir = os.path.join(store_dir, f"page_{page_num}")
        os.makedirs(page_dir, exist_ok=True)

        full_page_path = _get_pdf_page_image(pdf_path, page_num, dpi=150)
        if not full_page_path:
            continue

        # Step 1: Extract embedded images (QR codes, logos, inserted images)
        embedded = _extract_embedded_images(pdf_path, page_num, page_dir)

        # Step 2: Run Gemma vision to find actual diagrams with bounding boxes
        diagram_regions = []  # [{label, title, description, bbox}]
        if gemma_analysis:
            r = _gemma_analyze_image(full_page_path, task="diagram")
            if isinstance(r, dict) and "diagrams" in r:
                diagram_regions = r["diagrams"]
                # If diagram task found no diagrams, try "full" task as fallback
                if not diagram_regions:
                    r2 = _gemma_analyze_image(full_page_path, task="full")
                    if isinstance(r2, dict) and "diagrams" in r2 and r2["diagrams"]:
                        for d in r2["diagrams"]:
                            if isinstance(d, dict):
                                diagram_regions.append(
                                    {"label": "", "title": "", "description": d.get("description", ""), "bbox": None}
                                )
                            elif isinstance(d, str):
                                diagram_regions.append(
                                    {"label": "", "title": "", "description": d, "bbox": None}
                                )
            elif isinstance(r, dict) and "analysis" in r:
                # Fallback: use text description, no bounding boxes
                desc = r["analysis"]
                if desc and "No diagram" not in desc:
                    diagram_regions = [{"label": "", "title": "", "description": desc, "bbox": None}]

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

            if bbox and len(bbox) == 4:
                # Convert fractional coords (0-1000) to pixel coords
                x1 = int(bbox[0] / 1000 * pw)
                y1 = int(bbox[1] / 1000 * ph)
                x2 = int(bbox[2] / 1000 * pw)
                y2 = int(bbox[3] / 1000 * ph)
                # Validate
                if x2 > x1 and y2 > y1 and (x2 - x1) > 50 and (y2 - y1) > 50:
                    crop = page_img.crop((x1, y1, x2, y2))
                    crop_w, crop_h = crop.size
                    crop_fname = f"diagram_p{page_num}_{idx}.png"
                    cropped_path = os.path.join(page_dir, crop_fname)
                    crop.save(cropped_path)
                    crop_bbox = json.dumps([x1, y1, x2, y2])
                else:
                    # Bbox too small or invalid — use embedded image if available
                    cropped_path = None

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
                    break

                if not matched:
                    # Last resort: use the full page render itself
                    cropped_path = shutil_path
                    crop_w, crop_h = pw, ph
                    crop_bbox = json.dumps([0, 0, pw, ph])

            diag = {
                "pdf_id": pdf_id,
                "page_number": page_num,
                "image_path": cropped_path,
                "full_page_path": shutil_path,
                "description": description,
                "bbox": crop_bbox,
                "width": crop_w,
                "height": crop_h,
                "methods": json.dumps({"source": "cropped" if bbox else "full_page_render"}),
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
                      pages: list = None) -> list:
    """Extract diagrams and store them in the database.

    Returns list of inserted diagram dicts with their DB IDs.
    Only stores diagrams that pass the intentional diagram filter
    (has numbered figure labels, substantial descriptions, etc.).
    """
    diagrams = extract_diagrams(pdf_path, pdf_id=pdf_id, pages=pages)

    # Filter to only intentional diagrams before storing
    diagrams = filter_intentional_diagrams(diagrams)

    if db_conn and diagrams:
        for d in diagrams:
            db_conn.execute(
                """INSERT INTO pdf_diagrams
                   (pdf_id, page_number, image_path, full_page_path, description,
                    bbox, width, height, methods, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (d["pdf_id"], d["page_number"], d["image_path"], d["full_page_path"],
                 d["description"], d["bbox"], d["width"], d["height"],
                 d["methods"], d["status"]),
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
            trimmed = desc[:350].strip()
            parts.append(f"[Textbook Diagram on page {page + 1}: {trimmed}]")
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
