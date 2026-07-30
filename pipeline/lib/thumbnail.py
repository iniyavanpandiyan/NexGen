#!/usr/bin/env python3
"""
CBSE Shorts — social thumbnail generator (v1).

Pipeline:
  1. Extract a clean, high-contrast frame from the rendered final.mp4
     (early, before the end-card / outro).
  2. Send that frame + the title to Z-Image-Turbo (ComfyUI) to produce an
     on-brand cobalt/cream background illustration. Z-Image-Turbo is a
     text-to-image model and cannot render reliable text, so we keep text OUT
     of the generation and composite it in step 3.
  3. Composite the title + subject/kicker with brand-optimized thumbnail rules
     (readable at small size, bold, word-wrapped, safe-area aware).
  4. Export every platform size:
        YouTube 1280x720  (16:9  — also covers Shorts/Browse)
        Instagram 1080x1080 (square — feed posts)
        Instagram 1080x1350 (4:5  — portrait feed, best reach)
        Reels/Story 1080x1920 (9:16 — stories & reels)

Usage:
  python3 thumbnail.py --slug c9-ch1-part1 --title "The French Revolution" \
        --subject "History" --template science
  python3 thumbnail.py --slug c9-ch1-part1 --title "..." --auto   # pull title from script.json

Outputs go to: pipeline/rendered/<slug>/thumbs/{yt,ig_sq,ig_pt,reel}.png
and a manifest thumbs/manifest.json.
"""
import argparse, json, os, sys, subprocess, shutil, urllib.request, urllib.error
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "pipeline"
RENDERED = PIPELINE / "rendered"
TEMPLATES_DIR = PIPELINE / "templates"
COMFY = "http://127.0.0.1:8188"
COMFY_OUT = str(Path.home() / "comfy" / "output")

# Brand palette (defaults = Cobalt Grid / science). Templates override.
DEFAULT_THEME = {
    "paper": "#F4F1EA", "paper2": "#EAE5D8",
    "ink": "#1A3FB0", "ink_soft": "#5566C8", "accent": "#1A3FB0",
}
# Map subject -> a slightly different accent so subjects are visually distinct
# while staying inside the cobalt family (no alien hues).
SUBJECT_ACCENT = {
    "physics": "#1A3FB0", "science": "#1A3FB0", "maths": "#3A1F4D",
    "math": "#3A1F4D", "history": "#1A3FB0", "chemistry": "#1A3FB0",
}

# Optimised social export sizes.
FORMATS = {
    "yt":    (1280, 720),   # YouTube (16:9)
    "ig_sq": (1080, 1080),  # Instagram square (1:1)
    "ig_pt": (1080, 1350),  # Instagram portrait (4:5) — best reach
    "reel":  (1080, 1920),  # Reels / Stories (9:16)
}

# Fonts available on this box (closest to Newsreader/Hanken brand fonts).
DISPLAY_FONT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
BODY_FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
MONO_FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"


# --------------------------------------------------------------------------- color
def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def luminance(rgb):
    r, g, b = [c / 255.0 for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# --------------------------------------------------------------------------- comfy / z-image
def comfy_up():
    try:
        with urllib.request.urlopen(f"{COMFY}/system_stats", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_comfy():
    if comfy_up():
        return True
    # Reuse the gen_images supervisor so we don't hardcode the launch command.
    sup = PIPELINE / "lib" / "run_comfy.sh"
    try:
        subprocess.Popen(["bash", str(sup)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for _ in range(24):
        import time
        time.sleep(5)
        if comfy_up():
            return True
    return False


def _load_zimage():
    """Import Z-Image-Turbo graph constants from gen_images (single source of truth)."""
    sys.path.insert(0, str(PIPELINE / "lib"))
    import gen_images as G
    return G


def zimage_thumbnail(base_png, title, theme, seed=7, size=768, retries=3):
    """Run Z-Image-Turbo to produce an on-brand background from the frame + title."""
    G = _load_zimage()
    from PIL import Image
    # Z-Image-Turbo here is text-to-image; we feed a themed descriptive prompt.
    subject_hint = title or "educational concept"
    prompt = (
        f"editorial infographic illustration for a short video titled \"{subject_hint}\", "
        f"soft volumetric shading, strictly cobalt blue (#{theme['ink'].lstrip('#')}) tones "
        f"and cream (#{theme['paper'].lstrip('#')}) tones only, on a plain cream "
        f"(#{theme['paper'].lstrip('#')}) background, NO other colors, NO text, "
        f"clean centered composition, magazine cover art style"
    )
    for attempt in range(1, retries + 1):
        if not comfy_up() and not ensure_comfy():
            import time
            time.sleep(6)
            continue
        try:
            g = G.zimage_graph(prompt, seed, size=size)
            g["9"]["inputs"]["filename_prefix"] = "thumb"
            pid = G.post({"prompt": g, "client_id": "thumb"})["prompt_id"]
            h = G.history(pid)
            if "outputs" not in h:
                continue
            fname = h["outputs"]["9"]["images"][0]["filename"]
            src = os.path.join(G.COMFY_OUT, fname)
            if not os.path.exists(src):
                src = G.get_file_of_prefix("thumb")
            if not src or not os.path.exists(src):
                continue
            im = Image.open(src).convert("RGB")
            return im
        except Exception as e:
            print(f"[zimage] attempt {attempt} failed: {e}", file=sys.stderr)
            import time
            time.sleep(5)
    return None


# --------------------------------------------------------------------------- frame extract
def extract_frame(slug_dir, t="00:00:03"):
    """Pull a clean frame from final.mp4. Skip the very first/last seconds
    (intro logo / outro card)."""
    src = slug_dir / "final.mp4"
    if not src.exists():
        return None
    frame = slug_dir / "thumbs" / "_source_frame.png"
    frame.parent.mkdir(parents=True, exist_ok=True)
    # seek to t, grab one frame, scale to 720p for a stable base
    cmd = ["ffmpeg", "-y", "-ss", t, "-i", str(src), "-frames:v", "1",
           "-vf", "scale=1280:-1", "-q:v", "2", str(frame)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not frame.exists():
        # fallback: first frame
        cmd = ["ffmpeg", "-y", "-i", str(src), "-frames:v", "1",
               "-vf", "scale=1280:-1", "-q:v", "2", str(frame)]
        r2 = subprocess.run(cmd, capture_output=True, text=True)
        if r2.returncode != 0 or not frame.exists():
            return None
    return frame


# --------------------------------------------------------------------------- compositing
def wrap_text(draw, text, font, max_w):
    """Greedy word-wrap respecting max pixel width. Returns list of lines."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_font(draw, text, font_path, max_w, max_h, start=96, min_sz=28):
    """Decrease font size until the wrapped block fits (max_w x max_h)."""
    from PIL import ImageFont
    sz = start
    while sz >= min_sz:
        font = ImageFont.truetype(font_path, sz)
        lines = wrap_text(draw, text, font, max_w)
        line_h = int(sz * 1.12)
        total = line_h * len(lines)
        if total <= max_h:
            return font, lines, line_h
        sz -= 4
    font = ImageFont.truetype(font_path, min_sz)
    return font, wrap_text(draw, text, font, max_w), int(min_sz * 1.12)


def composite(background, title, theme, kicker, out_path, size):
    from PIL import Image, ImageDraw, ImageFilter
    W, H = size
    bg = background.resize((W, H), Image.LANCZOS)
    # subtle dark scrim at bottom for text legibility (still on-brand: cobalt-tinted)
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    ink = hex2rgb(theme["ink"])
    # bottom gradient scrim
    grad_h = int(H * 0.55)
    for i in range(grad_h):
        a = int(150 * (i / grad_h) ** 1.4)
        sd.line([(0, H - grad_h + i), (W, H - grad_h + i)],
                fill=(ink[0], ink[1], ink[2], a))
    bg = Image.alpha_composite(bg.convert("RGBA"), scrim).convert("RGB")

    draw = ImageDraw.Draw(bg)
    # safe margins (YouTube/IG safe areas)
    mx = int(W * 0.07)
    # kicker line — honor environment if present, else default
    kf = ImageFont.truetype(MONO_FONT, max(18, int(W * 0.022)))
    kt = kicker.upper()
    kbox_w = W - 2 * mx
    # fit kicker on one line, shrink if needed
    ks = max(18, int(W * 0.022))
    while kf.size > 18 and draw.textlength(kt, font=kf) > kbox_w:
        ks -= 2
        kf = ImageFont.truetype(MONO_FONT, ks)
    ky = int(H * 0.10)
    # kicker pill background
    kw = draw.textlength(kt, font=kf) + 28
    kh = int(ks * 1.8)
    draw.rectangle([mx, ky, mx + kw, ky + kh], fill=hex2rgb(theme["ink"]))
    draw.text((mx + 14, ky + kh / 2 - ks / 2), kt, font=kf,
              fill=hex2rgb(theme["paper"]))

    # title — fitted bold display, bottom-anchored with margin
    title_max_w = W - 2 * mx
    title_max_h = int(H * 0.5)
    tfont, tlines, tline_h = fit_font(draw, title, DISPLAY_FONT,
                                      title_max_w, title_max_h,
                                      start=max(40, int(W * 0.075)))
    block_h = tline_h * len(tlines)
    ty = H - int(H * 0.10) - block_h
    # text stroke for pop on busy backgrounds
    for li, line in enumerate(tlines):
        ly = ty + li * tline_h
        # outer stroke (cream) then fill (paper/white) for readability
        draw.text((mx, ly), line, font=tfont, fill=hex2rgb(theme["paper"]),
                  stroke_width=max(2, int(tline_h * 0.04)),
                  stroke_fill=hex2rgb(theme["ink"]))
    bg.save(out_path)
    return out_path


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--subject", default=None)
    ap.add_argument("--template", default="science")
    ap.add_argument("--auto", action="store_true",
                    help="pull title/subject from slug script.json")
    ap.add_argument("--frame-at", default="00:00:03")
    ap.add_argument("--skip-zimage", action="store_true",
                    help="skip Z-Image-Turbo; use extracted frame as background")
    args = ap.parse_args()

    slug_dir = RENDERED / args.slug
    if not slug_dir.exists():
        print(json.dumps({"error": f"slug not found: {args.slug}"}))
        return 1

    title, subject = args.title, args.subject
    if args.auto or not title:
        sj = slug_dir / "script.json"
        if sj.exists():
            sc = json.loads(sj.read_text())
            title = title or sc.get("title") or (sc.get("segments", [{}])[0].get("text", "")[:60])
            subject = subject or sc.get("subject")
    if not title:
        title = args.slug
    subject = subject or "Study"

    # theme
    theme = dict(DEFAULT_THEME)
    tp = TEMPLATES_DIR / f"{args.template}.json"
    if tp.exists():
        try:
            t = json.loads(tp.read_text())
            pal = t.get("palette", {})
            for k in ("paper", "paper2", "ink", "ink_soft", "accent"):
                if k in pal:
                    theme[k] = pal[k]
        except Exception:
            pass
    if subject.lower() in SUBJECT_ACCENT:
        theme["accent"] = SUBJECT_ACCENT[subject.lower()]

    kicker = f"{subject.title()} · Class {sc_class(slug_dir, args.slug)}".strip(" ·")

    out_dir = slug_dir / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. frame
    frame = extract_frame(slug_dir, t=args.frame_at)
    background = None
    if not args.skip_zimage:
        # 2. Z-Image-Turbo background (enabled by default)
        print(f"[thumb] generating Z-Image-Turbo background for '{title}'…",
              file=sys.stderr)
        background = zimage_thumbnail(frame, title, theme, seed=7, size=768)
        if background is None:
            print("[thumb] Z-Image-Turbo unavailable — falling back to raw frame",
                  file=sys.stderr)
    if background is None:
        from PIL import Image
        background = Image.open(frame).convert("RGB") if frame else \
            Image.new("RGB", (1280, 720), hex2rgb(theme["paper"]))

    # 3+4. composite + export all formats
    manifest = {}
    for fmt, sz in FORMATS.items():
        out = out_dir / f"{fmt}.png"
        composite(background, title, theme, kicker, str(out), sz)
        manifest[fmt] = {"file": f"thumbs/{fmt}.png", "w": sz[0], "h": sz[1]}
        print(f"[thumb] wrote {fmt} {sz[0]}x{sz[1]} -> {out}", file=sys.stderr)

    json.dump({"slug": args.slug, "title": title, "subject": subject,
               "template": args.template, "formats": manifest},
              open(out_dir / "manifest.json", "w"), indent=2)
    print(json.dumps({"slug": args.slug, "title": title, "thumbs": manifest}))


def sc_class(slug_dir, slug):
    sj = slug_dir / "script.json"
    if sj.exists():
        try:
            return json.loads(sj.read_text()).get("class", "")
        except Exception:
            pass
    return ""


if __name__ == "__main__":
    sys.exit(main() or 0)
