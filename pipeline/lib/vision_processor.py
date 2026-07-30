"""NCERT Vision Processor — local multimodal PDF page analysis via llama.cpp.

Extracts images from PDF pages and sends them to llama-server (Gemma 4 E2B)
for structured analysis: diagram descriptions, activity boxes, side-notes,
and hooks for video script generation.

Usage:
    python vision_processor.py <pdf_path> [--page 0] [--serve]

Requires:
    - llama-server running on localhost:8081 (or set LLAMA_HOST env var)
    - poppler-utils (apt install poppler-utils) for pdfimages
    - Pillow (pip install Pillow)
"""

import os
import sys
import json
import subprocess
import tempfile
import requests
from pathlib import Path
from typing import Optional

LLAMA_HOST = os.environ.get("LLAMA_HOST", "http://127.0.0.1:8082")
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# PDF → Image extraction
# ---------------------------------------------------------------------------

def page_to_image(pdf_path: str, page_num: int = 0, dpi: int = 100) -> Optional[str]:
    """Convert a PDF page to a PNG image using pdftoppm or ImageMagick.

    Uses 100 DPI default (washes out fine detail but keeps diagrams readable).
    Returns path to the saved PNG.
    """
    out_dir = tempfile.mkdtemp(prefix="ncert-vision-")
    stem = Path(pdf_path).stem

    # Try pdftoppm first (faster, native)
    out_path = os.path.join(out_dir, f"{stem}_p{page_num}.png")
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-f", str(page_num + 1), "-l", str(page_num + 1),
             "-r", str(dpi), "-singlefile", pdf_path, os.path.join(out_dir, f"{stem}_p{page_num}")],
            capture_output=True, timeout=30, check=True
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: use convert (ImageMagick)
    try:
        subprocess.run(
            ["convert", "-density", str(dpi), f"{pdf_path}[{page_num}]",
             "-quality", "90", "-resize", "1920x", out_path],
            capture_output=True, timeout=30, check=True
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


# ---------------------------------------------------------------------------
# Gemma 4 multimodal inference via llama-server
# ---------------------------------------------------------------------------

def analyze_page(image_path: str, task: str = "diagram", context_prefix: str = "") -> dict:
    """Send an image to Gemma 4 via llama-server and return structured analysis.

    Args:
        image_path: Path to the PNG image
        task: one of "diagram", "activity", "hook", "full"
        context_prefix: optional context string prepended to the prompt
                       (e.g. page summary from comprehensive analysis)

    Returns:
        dict with 'analysis' (str), 'type' (str), and optionally 'error'
    """
    # Read image as base64
    import base64
    with open(image_path, "rb") as f:
        b64_image = base64.b64encode(f.read()).decode("utf-8")

    prompts = {
        "diagram": (
            "You are a science education expert analyzing a textbook page. "
            "Describe any diagram, figure, chart, or illustration on this page. "
            "Include: what it shows, key labels, colors/visual elements, "
            "and how it relates to the surrounding text. "
            "If there is no diagram, say 'No diagram on this page.'"
        ),
        "activity": (
            "You are a science education expert. Scan this textbook page for "
            "activity boxes, experiments, 'Think It Over' sections, side notes, "
            "hints, tips, or margin callouts. Describe exactly what each one says "
            "and where it appears on the page (margin, sidebar, below main text). "
            "If there are none, say 'No activity boxes on this page.'"
        ),
        "hook": (
            "You are a scriptwriter for educational YouTube shorts. Look at this "
            "textbook page and identify 1-2 engaging hook ideas that could grab "
            "a student's attention in the first 3 seconds of a 60-second video. "
            "Consider surprising facts, counterintuitive statements, or relatable "
            "questions rooted in the content. Also identify interesting cliffhanger "
            "questions or 'what happens next' moments for the end of segments."
        ),
        "full": (
            "You are analyzing a textbook page for video script generation. "
            "Return a JSON object with these fields:\n"
            "- main_text: the core explanatory text (2-3 sentences)\n"
            "- activity_text: any activity/experiment/lab instructions verbatim\n"
            "- side_notes: any tips, hints, margin notes, or callout boxes verbatim\n"
            "- diagrams: descriptions of all diagrams, charts, illustrations\n"
            "- hook_idea: one engaging hook for a YouTube short\n"
            "- cliffhanger: one cliffhanger question for segment transitions\n"
            "If a field has no content, use null."
        ),
        "chapter": (
            "You are analyzing the first page of a CBSE/NCERT textbook chapter. "
            "Return a JSON object with exactly these fields:\n"
            "- chapter_number: the chapter number as an integer\n"
            "- chapter_name: the exact chapter title as written on the page\n"
            "- subject: the subject (Science, Mathematics, etc.)\n"
            "- class: the class number as an integer\n"
            "Look at the header, title area, and any heading that has the chapter "
            "number and name. Return ONLY valid JSON, no other text."
        ),
    }

    base_prompt = prompts.get(task, prompts["full"])
    prompt = (context_prefix + "\n\n" + base_prompt) if context_prefix else base_prompt

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}},
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    try:
        resp = requests.post(
            f"{LLAMA_HOST}/v1/chat/completions",
            json=payload,
            timeout=120,
        )
        if resp.status_code != 200:
            return {"error": f"API error {resp.status_code}: {resp.text[:200]}", "type": task}

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # Try to parse JSON if 'full' task
        if task == "full":
            try:
                parsed = json.loads(content)
                return {"analysis": content, "parsed": parsed, "type": task}
            except (json.JSONDecodeError, KeyError):
                pass

        return {"analysis": content, "type": task}

    except requests.exceptions.ConnectionError:
        return {"error": "Cannot connect to llama-server. Is it running?", "type": task}
    except Exception as e:
        return {"error": str(e), "type": task}


# ---------------------------------------------------------------------------
# Full page analysis pipeline
# ---------------------------------------------------------------------------

def analyze_pdf_page(pdf_path: str, page_num: int = 0) -> dict:
    """Full analysis of a single PDF page: diagram + activity + hook."""
    img = page_to_image(pdf_path, page_num)
    if not img:
        return {"page": page_num, "error": "Could not render page as image"}

    results = {}
    for task in ["diagram", "activity", "hook"]:
        r = analyze_page(img, task)
        results[task] = r

    # Clean up temp image
    try:
        os.remove(img)
        os.rmdir(os.path.dirname(img))
    except OSError:
        pass

    return {"page": page_num, **results}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="NCERT Vision Processor")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("--page", "-p", type=int, default=0, help="Page number (0-indexed)")
    parser.add_argument("--task", "-t", choices=["diagram", "activity", "hook", "full"], default="full",
                        help="Analysis type")
    parser.add_argument("--serve", action="store_true", help="Start llama-server in background")
    parser.add_argument("--output", "-o", help="Save result to JSON file")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"Error: {args.pdf} not found")
        sys.exit(1)

    if args.serve:
        print("Starting llama-server ...")
        model_path = os.path.expanduser("~/models/gemma-4/gemma-4-E2B-it-Q4_K_M.gguf")
        mmproj_path = os.path.expanduser("~/models/gemma-4/mmproj-F16.gguf")
        if not os.path.exists(model_path):
            print(f"Model not found at {model_path}")
            sys.exit(1)
        if not os.path.exists(mmproj_path):
            print(f"mmproj not found at {mmproj_path}")
            sys.exit(1)

        proc = subprocess.Popen(
            ["llama-server", "-m", model_path, "--mmproj", mmproj_path,
             "--host", "127.0.0.1", "--port", "8081",
             "-ngl", "99", "-c", "8192", "--parallel", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"llama-server started (PID {proc.pid}) on :8081")
        import time
        time.sleep(8)  # wait for warmup

    img = page_to_image(args.pdf, args.page)
    if not img:
        print("Failed to render PDF page as image")
        sys.exit(1)

    print(f"Rendered page {args.page} → {img}")
    result = analyze_page(img, args.task)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Saved to {args.output}")

    # Cleanup
    try:
        os.remove(img)
        os.rmdir(os.path.dirname(img))
    except OSError:
        pass


if __name__ == "__main__":
    main()