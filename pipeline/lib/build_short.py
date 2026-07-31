#!/usr/bin/env python3
"""
CBSE Shorts — generation pipeline (v4).

Design: official HyperFrames "Cobalt Grid" frame preset — warm cream paper,
electric cobalt ink (the ONLY ink), permanent graph-paper grid, top/bottom
cobalt hairlines, Newsreader serif display + Hanken Grotesk body + DM Mono
chrome. Two-color ONLY, square corners, flat (no drop shadows).

v4 adds:
  * a richer animated vector layer — a slowly rotating background ring + drifting
    constellation dots that persist across the whole video, plus per-scene vector
    flourishes (an orbit ring that spins around the icon, a cobalt frame that
    draws itself around each generated image, animated corner ticks).
  * one ComfyUI-generated flat illustration per slide, framed in cobalt.
  * a themed animated SVG icon per slide (inferred from the segment text).
  * word-level karaoke synced to REAL Groq Whisper word timings.

Narration: OmniVoice TTS via local ComfyUI endpoint. Falls back to edge-tts with --tts edge.
"""
import argparse, json, os, re, subprocess, sys, shutil, time, urllib.request, urllib.error, difflib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib.svg_scenes import scene_for_segment, generate_svg_scene

VENV = "/home/fiipadmin/.hermes/hermes-agent/venv"
EDGE_TTS = os.path.join(VENV, "bin", "edge-tts")
PY = os.path.join(VENV, "bin", "python")
ROOT = "/home/fiipadmin/workspace/NexGen"
TEMPLATES_DIR = os.path.join(ROOT, "pipeline", "templates")

# Default (science / Cobalt Grid) palette + fonts. Templates override these.
DEFAULT_THEME = {
    "paper": "#F4F1EA", "paper2": "#EAE5D8",
    "ink": "#1A3FB0", "ink_soft": "#5566C8", "accent": "#1A3FB0",
    "grid": "rgba(26,63,176,0.10)", "ink_faint": "rgba(26,63,176,0.18)",
    "display": "Newsreader", "body": "Hanken Grotesk", "mono": "DM Mono",
    "google": "Newsreader:ital,wght@0,400;1,400&family=Hanken+Grotesk:wght@400;600;700&family=DM+Mono:wght@400;500",
}

def load_theme(template_id):
    tid = (template_id or "science").strip() or "science"
    p = os.path.join(TEMPLATES_DIR, f"{tid}.json")
    if os.path.exists(p):
        try:
            t = json.load(open(p))
            pal = t.get("palette", {})
            fnt = t.get("fonts", {})
            return {
                "paper": pal.get("paper", DEFAULT_THEME["paper"]),
                "paper2": pal.get("paper2", DEFAULT_THEME["paper2"]),
                "ink": pal.get("ink", DEFAULT_THEME["ink"]),
                "ink_soft": pal.get("ink_soft", DEFAULT_THEME["ink_soft"]),
                "accent": pal.get("accent", DEFAULT_THEME["ink"]),
                "grid": pal.get("grid", DEFAULT_THEME["grid"]),
                "ink_faint": pal.get("ink_faint", DEFAULT_THEME["ink_faint"]),
                "display": fnt.get("display", DEFAULT_THEME["display"]),
                "body": fnt.get("body", DEFAULT_THEME["body"]),
                "mono": fnt.get("mono", DEFAULT_THEME["mono"]),
                "google": fnt.get("google", DEFAULT_THEME["google"]),
            }
        except Exception:
            pass
    return dict(DEFAULT_THEME)


# ---- inline SVG icon set (cobalt stroke, viewBox 0 0 100 100) ----------------
ICONS = {
    "book": '<path d="M20 24 H48 a6 6 0 0 1 6 6 V84 l-8-5-8 5-8-5-8 5 V30 a6 6 0 0 1 6-6 Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M52 24 H80 a6 6 0 0 1 6 6 V84 l-8-5-8 5-8-5-8 5 V30 a6 6 0 0 1 6-6 Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/>',
    "flask": '<path d="M42 18 H58 M45 18 V44 L24 82 a6 6 0 0 0 5 9 H71 a6 6 0 0 0 5-9 L55 44 V18" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/><path d="M34 64 H66" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>',
    "atom": '<circle cx="50" cy="50" r="9" fill="currentColor"/><ellipse cx="50" cy="50" rx="38" ry="15" fill="none" stroke="currentColor" stroke-width="4"/><ellipse cx="50" cy="50" rx="38" ry="15" fill="none" stroke="currentColor" stroke-width="4" transform="rotate(60 50 50)"/><ellipse cx="50" cy="50" rx="38" ry="15" fill="none" stroke="currentColor" stroke-width="4" transform="rotate(120 50 50)"/>',
    "leaf": '<path d="M26 80 C26 40 60 22 80 22 C80 62 48 82 26 80 Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M30 76 C46 60 62 46 78 30" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>',
    "math": '<path d="M22 80 V20 M22 50 H82 M22 80 H82" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M30 40 C46 30 60 70 78 60" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>',
    "bulb": '<path d="M50 20 a22 22 0 0 1 14 39 c-3 3-5 6-5 11 H41 c0-5-2-8-5-11 A22 22 0 0 1 50 20 Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M42 80 H58 M44 88 H56" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>',
    # --- expanded thematic motifs (v4) ---
    "cube": '<path d="M50 16 L82 34 V66 L50 84 L18 66 V34 Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M50 16 V50 L82 34 M50 50 L18 34 M50 50 V84" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/>',
    "drop": '<path d="M50 16 C50 16 28 46 28 64 a22 22 0 0 0 44 0 C72 46 50 16 50 16 Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/>',
    "scatter": '<g fill="currentColor"><circle cx="28" cy="30" r="6"/><circle cx="62" cy="24" r="5"/><circle cx="74" cy="52" r="7"/><circle cx="40" cy="60" r="5"/><circle cx="30" cy="74" r="6"/><circle cx="66" cy="76" r="5"/></g>',
    "arrows": '<path d="M22 36 C40 20 60 20 78 36" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M78 30 L78 44 L64 38 Z" fill="currentColor"/><path d="M78 64 C60 80 40 80 22 64" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M22 58 L22 72 L36 66 Z" fill="currentColor"/>',
    "magnet": '<path d="M32 20 V54 a18 18 0 0 0 36 0 V20" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M32 20 H40 V54 a10 10 0 0 0 20 0 V20 H48" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/>',
    "cycle": '<path d="M50 22 a28 28 0 1 1 -20 48" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M24 58 L22 76 L40 71 Z" fill="currentColor"/><path d="M78 70 a28 28 0 0 1 -30 22" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" opacity="0.5"/>',
    "wave": '<path d="M14 54 q12 -18 24 0 t24 0 t24 0" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M14 70 q12 -18 24 0 t24 0 t24 0" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" opacity="0.55"/>',
    "heat": '<circle cx="50" cy="50" r="14" fill="none" stroke="currentColor" stroke-width="4"/><g stroke="currentColor" stroke-width="4" stroke-linecap="round"><path d="M50 18 V30"/><path d="M50 70 V82"/><path d="M18 50 H30"/><path d="M70 50 H82"/><path d="M28 28 L36 36"/><path d="M64 64 L72 72"/><path d="M72 28 L64 36"/><path d="M36 64 L28 72"/></g>',
    "funnel": '<path d="M26 24 H74 L56 52 V84 H44 V52 Z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/>',
    "piston": '<rect x="26" y="42" width="48" height="42" rx="6" fill="none" stroke="currentColor" stroke-width="4"/><path d="M22 42 H78" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M50 22 V42" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M38 18 H62" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>',
    "evap": '<g fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"><path d="M36 80 V46 l-8 8 M36 46 l8 8"/><path d="M64 80 V46 l-8 8 M64 46 l8 8"/></g>',
}
SUBJECT_ICON = {
    "science": "leaf", "biology": "leaf", "physics": "atom", "chemistry": "flask",
    "math": "math", "mathematics": "math", "english": "book", "history": "book",
    "geography": "book", "civics": "book", "economics": "math", "default": "bulb",
}
# ordered keyword -> motif (first match wins)
KEYWORD_ICON = [
    ("sublim", "cycle"), ("evapor", "evap"), ("latent", "funnel"),
    ("heat", "heat"), ("melt", "heat"), ("boil", "heat"), ("steam", "heat"),
    ("compress", "piston"), ("pressure", "piston"),
    ("attract", "magnet"),
    ("mix", "arrows"), ("moving", "arrows"), ("motion", "arrows"),
    ("liquid", "wave"), ("flow", "wave"),
    ("gas", "scatter"),
    ("dissolv", "drop"), ("water", "drop"),
    ("particle", "scatter"), ("tiny", "scatter"),
    ("solid", "cube"),
]

def icon_for(subject):
    s = (subject or "").lower()
    for k, v in SUBJECT_ICON.items():
        if k in s:
            return v
    return SUBJECT_ICON["default"]

def icon_for_segment(seg, subject):
    if seg.get("icon") and seg["icon"] in ICONS:
        return seg["icon"]
    blob = (seg.get("text", "") + " " + seg.get("image_prompt", "")).lower()
    for kw, ic in KEYWORD_ICON:
        if kw in blob:
            return ic
    return icon_for(subject)

def chunk_text(text, max_chars=300, min_chars=40):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\b([a-zA-Z])\s([a-z]{2,})\b",
                  lambda m: m.group(1) + m.group(2), text)
    raw = re.split(r"(?<=[.!?])\s+", text)
    segs, buf = [], ""
    for s in raw:
        s = s.strip()
        if not s:
            continue
        if buf and len(buf) + len(s) + 1 > max_chars:
            segs.append(buf.strip()); buf = s
        else:
            buf = (buf + " " + s).strip() if buf else s
    if buf:
        segs.append(buf.strip())
    out = []
    for s in segs:
        if out and len(s) < min_chars:
            out[-1] = (out[-1] + " " + s).strip()
        else:
            out.append(s)
    return out

# --------------------------- TTS backends ------------------------------------
def tts_omnivoice(seg_text, out_wav):
    omni = {
        "3": {"class_type": "OmniVoiceLongformTTS", "inputs": {
            "model": "OmniVoice", "text": seg_text, "ref_text": "",
            "steps": 32, "guidance_scale": 2.0, "t_shift": 0.1,
            "speed": 1.0, "duration": 0.0,
            "device": "auto", "dtype": "auto", "attention": "auto", "seed": 0,
            "words_per_chunk": 0,
            "position_temperature": 5.0, "class_temperature": 0.0,
            "layer_penalty_factor": 5.0,
            "denoise": True, "preprocess_prompt": True, "postprocess_output": True,
            "keep_model_loaded": True, "instruct": ""
        }},
        "9": {"class_type": "SaveAudio", "inputs": {"audio": ["3", 0], "filename_prefix": "cbse_tts"}},
    }
    data = json.dumps({"prompt": omni, "client_id": "cbse-tts"}).encode()
    req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        pid = json.loads(r.read().decode())["prompt_id"]
    dl = time.time() + 240
    while time.time() < dl:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8188/history/{pid}", timeout=10) as r:
                h = json.loads(r.read().decode())
        except Exception:
            time.sleep(2); continue
        if pid in h:
            fname = h[pid]["outputs"]["9"]["audio"][0]["filename"]
            break
        time.sleep(2)
    src = "/home/fiipadmin/comfy/ComfyUI/output/" + fname
    shutil.copy(src, out_wav)

def tts_edge(seg_text, out_wav, voice):
    mp3 = out_wav[:-4] + ".mp3"
    r = subprocess.run([EDGE_TTS, "--voice", voice, "--text", seg_text,
                        "--write-media", mp3], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(mp3):
        raise RuntimeError(f"edge-tts failed: {r.stderr[:400]}")
    subprocess.run(["ffmpeg", "-y", "-i", mp3, out_wav], capture_output=True, check=True)
    os.remove(mp3)

def probe_duration(wav):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of",
                        "default=noprint_wrappers=1:nokey=1", wav],
                       capture_output=True, text=True)
    return float(r.stdout.strip())

def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

# --------------------------- word alignment (Groq Whisper) -------------------
def _groq_key():
    try:
        from hermes_cli.config import get_env_value
        k = get_env_value("GROQ_API_KEY")
        if k:
            return k
    except Exception:
        pass
    return os.getenv("GROQ_API_KEY")

def transcribe_groq(wav_path):
    try:
        from openai import OpenAI
    except ImportError:
        return None
    key = _groq_key()
    if not key:
        return None
    try:
        client = OpenAI(api_key=key,
                        base_url="https://api.groq.com/openai/v1",
                        timeout=120, max_retries=1)
        with open(wav_path, "rb") as f:
            r = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo", file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"], language="en")
        d = r.model_dump() if hasattr(r, "model_dump") else json.loads(r.model_dump_json())
        words = d.get("words") or []
        return words if words else None
    except Exception as e:
        print(f"[warn] groq whisper error: {e}", file=sys.stderr)
        return None

def normalize_token(tok):
    return re.sub(r"[^a-z0-9]", "", tok.lower())

def align_words(script_words, whisper_words):
    n = len(script_words)
    if n == 0:
        return []
    if not whisper_words:
        return None
    s_norm = [normalize_token(w) for w in script_words]
    w_norm = [normalize_token(w.get("word", "")) for w in whisper_words]
    sm = difflib.SequenceMatcher(a=s_norm, b=w_norm, autojunk=False)
    w_idx = 0
    out = [None] * n
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                w = whisper_words[j1 + (k - i1)]
                out[k] = (w["start"], w["end"])
        elif tag == "replace":
            wblock = whisper_words[j1:j2]
            if wblock:
                ts, te = wblock[0]["start"], wblock[-1]["end"]
                for k in range(i1, i2):
                    out[k] = (ts, te)
        elif tag == "delete":
            for k in range(i1, i2):
                out[k] = None
        w_idx = j2
    prev_end = whisper_words[0]["start"]
    for k in range(n):
        if out[k] is None:
            if k + 1 < n and out[k + 1] is not None:
                out[k] = (prev_end, out[k + 1][0])
            else:
                out[k] = (prev_end, prev_end + 0.4)
        prev_end = out[k][1]
    if any(o is None for o in out):
        return None
    return out

def align_segment_times(abs_start, dur, script_words, whisper_words):
    n = len(script_words)
    if n == 0:
        return []
    if not whisper_words:
        return [(abs_start + (k / n) * dur, abs_start + ((k + 1) / n) * dur)
                for k in range(n)]
    w_in = [w for w in whisper_words
            if abs_start <= w["start"] < abs_start + dur + 0.001]
    if not w_in:
        return [(abs_start + (k / n) * dur, abs_start + ((k + 1) / n) * dur)
                for k in range(n)]
    aligned = align_words(script_words, w_in)
    if aligned is None:
        t0, t1 = w_in[0]["start"], w_in[-1]["end"]
        return [(t0 + (t1 - t0) * (k / n), t0 + (t1 - t0) * ((k + 1) / n))
                for k in range(n)]
    return aligned

# --------------------------- HTML assembly -----------------------------------
def _is_light(hex_color):
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return True
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) > 128

def build_html(seq, title, subject, cls, slug, whisper_words=None, part="",
               w=1080, h=1920, theme=None, svg_mode=False, diagrams=None):
    """seq: list of dicts {index, text, start, duration, image, icon}.
    If svg_mode=True, image field is ignored and inline SVG scenes are generated instead.
    diagrams: dict mapping segment index -> {'path': str, 'description': str} for textbook diagram overlay."""
    theme = theme or DEFAULT_THEME
    PAPER = theme["paper"]; PAPER2 = theme["paper2"]; INK = theme["ink"]
    INK_SOFT = theme["ink_soft"]; ACCENT = theme["accent"]
    GRID = theme["grid"]; INK_FAINT = theme["ink_faint"]
    F_DISPLAY = theme["display"]; F_BODY = theme["body"]; F_MONO = theme["mono"]
    GOOGLE = theme["google"]
    dark_mode = not _is_light(PAPER)
    if dark_mode:
        F_DISPLAY_BODY = "Inter"
    else:
        F_DISPLAY_BODY = F_BODY
    total = round(sum(s["start"] + s["duration"] for s in seq), 3)
    eases = ["power2.out","power3.out","back.out(1.4)","elastic.out(1,0.3)","bounce.out","sine.out","expo.out","circ.out"]
    dirs_x = [-60,60,-40,40,0,0,-80,80]
    dirs_y = [40,40,-30,-30,60,-50,20,0]
    img_scales = [0.92,0.85,0.78,0.95,0.88,0.82,0.9,0.86]
    ico_rotates = [-18,22,-25,30,-15,20,-28,35]
    total = round(max([s["start"] + s["duration"] for s in seq] + [0]), 3)
    scenes, tl = [], []

    for i, s in enumerate(seq):
        start = round(s["start"], 3)
        dur = round(s["duration"], 3)
        icon = s["icon"]
        img = s.get("image")
        words = re.findall(r"\S+", s["text"])
        n = max(1, len(words))
        word_times = align_segment_times(start, dur, words, whisper_words or [])
        spans = []
        for wi, wd in enumerate(words):
            ws, we = word_times[wi]
            eid = f"w-{i}-{wi}"
            spans.append((eid, wd, round(ws, 3), round(we, 3)))
        word_html = "".join(
            f'<span class="kw" id="{eid}">{esc(wd)}</span>'
            for eid, wd, _, _ in spans)
        cap = esc(s["text"])

        img_card = ""
        if svg_mode:
            sc_type = scene_for_segment(s["text"], subject)
            sc_svg, sc_tl = generate_svg_scene(sc_type, i, INK, INK_SOFT, PAPER, dark_mode)
            diag = (diagrams or {}).get(str(i)) or (diagrams or {}).get(i)
            diag_bg = ""
            if diag and diag.get("path"):
                dp = diag["path"]
                # Make path relative from rendered/{slug}/ back to assets/pdfs/
                rel = dp
                if "assets/pdfs/" in dp:
                    rel = "../../" + dp.split("assets/pdfs/", 1)[1].lstrip("/")
                    rel = "../../assets/pdfs/" + rel.split("assets/pdfs/", 1)[1] if "assets/pdfs/" in rel else rel
                # Simpler: find assets/ in path and go relative
                if "/assets/" in dp:
                    rel = "../" * 3 + dp.split("/assets/", 1)[1]
                diag_bg = f'''
            <img src="{esc(rel)}" class="diagram-overlay" id="diag-{i}"
                 style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain;opacity:0;pointer-events:none"
                 alt="textbook diagram" loading="lazy"/>'''
                tl.append(f'      tl.to("#diag-{i}", {{ opacity: 0.35, duration: 0.8, ease: "power2.out" }}, {start} + 0.05);')
            if sc_svg:
                img_card = f'''
          <div class="svg-scene-card" id="svgc-{i}" style="position:relative;overflow:hidden">
            {diag_bg}
            {sc_svg}
          </div>'''
                tl.extend(sc_tl)
            else:
                img_card = ""
        elif img:
            img_card = f'''
          <div class="image-card" id="img-{i}">
            <img src="{esc(img)}" alt="" />
            <svg class="frame" viewBox="0 0 100 100" preserveAspectRatio="none">
              <rect class="frame-rect" id="frect-{i}" x="2" y="2" width="96" height="96"
                fill="none" stroke="currentColor" stroke-width="1.4"
                stroke-dasharray="400" stroke-dashoffset="400"/>
            </svg>
            <svg class="corners" id="corn-{i}" viewBox="0 0 100 100" preserveAspectRatio="none">
              <g fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round">
                <path d="M2 16 V2 H18"/><path d="M84 2 H98 V16"/>
                <path d="M98 84 V98 H82"/><path d="M18 98 H2 V82"/>
              </g>
            </svg>
          </div>'''

        scenes.append(f'''
      <section id="scene-{i}" class="clip scene" data-start="{start}" data-duration="{dur}" data-track-index="1">
        <div class="scene-inner" id="inner-{i}">
{img_card}
          <div class="caption-row">
            <div class="ico" id="ico-{i}">
              <svg viewBox="0 0 100 100" class="ico-svg">{ICONS[icon]}</svg>
              <svg viewBox="0 0 100 100" class="orbit" id="orbit-{i}">
                <circle cx="50" cy="50" r="46" fill="none" stroke="currentColor"
                  stroke-width="2" stroke-dasharray="2 6" opacity="0.55"/>
              </svg>
            </div>
            <p class="caption" id="cap-{i}">{word_html}</p>
          </div>
          <p class="caption-sr" data-text="{cap}"></p>
        </div>
      </section>''')

        end = round(start + dur, 3)
        # --- entrance choreography (varied per scene) ---
        es = eases[i % len(eases)]
        ey = dirs_y[i % len(dirs_y)]
        ex = dirs_x[i % len(dirs_x)]
        ims = img_scales[i % len(img_scales)]
        ico_r = ico_rotates[i % len(ico_rotates)]
        if svg_mode:
            tl.append(f'      tl.fromTo("#svgc-{i}", {{ opacity: 0, y: 15 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: "{es}" }}, {start} + 0.05);')
        elif img:
            tl.append(f'      tl.fromTo("#img-{i}", {{ scale: {ims}, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.6, ease: "{es}" }}, {start} + 0.05);')
            tl.append(f'      tl.fromTo("#frect-{i}", {{ strokeDashoffset: 400 }}, {{ strokeDashoffset: 0, duration: 0.7, ease: "{eases[(i+1) % len(eases)]}" }}, {start} + 0.05);')
            tl.append(f'      tl.fromTo("#corn-{i}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5, ease: "{eases[(i+2) % len(eases)]}" }}, {start} + 0.22);')
        tl.append(f'      tl.fromTo("#ico-{i} .ico-svg", {{ scale: 0, rotate: {ico_r}, transformOrigin: "50% 50%" }}, {{ scale: 1, rotate: 0, duration: 0.6, ease: "{eases[(i+3) % len(eases)]}" }}, {start} + 0.18);')
        tl.append(f'      tl.fromTo("#ico-{i} .ico-svg", {{ strokeDashoffset: 260, opacity: 0 }}, {{ strokeDashoffset: 0, opacity: 1, duration: 0.7, ease: "{eases[(i+4) % len(eases)]}" }}, {start} + 0.18);')
        tl.append(f'      tl.fromTo("#orbit-{i}", {{ rotation: 0, transformOrigin: "50% 50%" }}, {{ rotation: 360, transformOrigin: "50% 50%", duration: {max(2.6, round(dur,2))}, ease: "none" }}, {start});')
        tl.append(f'      tl.fromTo("#cap-{i}", {{ y: {ey}, opacity: 0 }}, {{ y: 0, opacity: 1, duration: 0.5, ease: "{eases[(i+5) % len(eases)]}" }}, {start} + 0.12);')
        # --- word karaoke (seek-safe) ---
        for eid, wd, ws, we in spans:
            tl.append(f'      tl.set("#{eid}", {{ className: "kw is-active" }}, {ws});')
            tl.append(f'      tl.set("#{eid}", {{ className: "kw is-spoken" }}, {we});')
            tl.append(f'      tl.fromTo("#{eid}", {{ scale: 0.96 }}, {{ scale: 1, duration: 0.12, ease: "power3.out" }}, {ws});')
        # --- exit (hard kill at next scene boundary) ---
        if i + 1 < len(seq):
            nx = round(seq[i + 1]["start"], 3)
            tl.append(f'      tl.to("#inner-{i}", {{ y: -30, opacity: 0, duration: 0.35, ease: "power2.in" }}, {nx} - 0.35);')
            tl.append(f'      tl.set("#inner-{i}", {{ opacity: 0, y: -30 }}, {nx});')
        else:
            tl.append(f'      tl.set("#inner-{i}", {{ opacity: 0 }}, {total} - 0.02);')

    scenes_str = "\n".join(scenes)
    tl_str = "\n".join(tl)

    # ---- build CSS ----
    if dark_mode:
        ambient_divs = f'''
      <div id="glow-cyan"></div>
      <div id="glow-magenta"></div>'''
        scene_inner_css = f'''
      .scene-inner {{ max-width: 900px; width: 100%;
        display: flex; flex-direction: column; align-items: center; gap: 32px; }}
      .caption-row {{ display: flex; align-items: center; gap: 28px;
        width: 100%; max-width: 820px;
        background: {theme.get("surface","rgba(18,23,38,0.75)")};
        backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(228,232,240,0.08);
        border-radius: 20px; padding: 24px 28px; }}'''
        progress_css = f'''
      .progress {{ position: absolute; left: 0; bottom: 0; height: 4px; width: 0%;
        background: linear-gradient(90deg, {ACCENT}, #E64DCC); z-index: 6; }}'''
        bg_dots_css = '''
      .bg-dots { position: absolute; inset: 0; z-index: 0;
        color: rgba(228,232,240,0.15); pointer-events: none; }'''
        hairline_css = f'''
      .hairline {{ position: absolute; left: 64px; right: 64px; height: 1px;
        background: linear-gradient(90deg, transparent, {ACCENT}, transparent); z-index: 4; }}'''
        glow_css = f'''
      #glow-cyan {{ position: absolute; left: 0; top: 0; width: 100%; height: 100%;
        background: radial-gradient(ellipse 60% 50% at 25% 30%, {theme.get("glow_cyan","rgba(34,217,160,0.08)")} 0%, transparent 70%);
        z-index: 0; pointer-events: none; }}
      #glow-magenta {{ position: absolute; left: 0; top: 0; width: 100%; height: 100%;
        background: radial-gradient(ellipse 50% 60% at 75% 70%, {theme.get("glow_magenta","rgba(230,77,204,0.06)")} 0%, transparent 70%);
        z-index: 0; pointer-events: none; }}'''
        chap_css = f'''
      .chap {{ font-family: "{F_BODY}", system-ui, sans-serif; font-weight: 600;
        font-size: 30px; letter-spacing: -0.01em; color: {INK}; line-height: 1.2; }}'''
    else:
        ambient_divs = ''
        glow_css = ''
        scene_inner_css = f'''
      .scene-inner {{ max-width: 900px; width: 100%;
        display: flex; flex-direction: column; align-items: center; gap: 46px; }}
      .caption-row {{ display: flex; align-items: center; gap: 28px;
        width: 100%; max-width: 820px; }}'''
        progress_css = f'''
      .progress {{ position: absolute; left: 0; bottom: 0; height: 8px; width: 0%;
        background: {ACCENT}; z-index: 6; }}'''
        bg_dots_css = f'''
      .bg-dots {{ position: absolute; inset: 0; z-index: 0;
        color: {INK_SOFT}; opacity: 0.4; pointer-events: none; }}'''
        hairline_css = f'''
      .hairline {{ position: absolute; left: 64px; right: 64px; height: 2px;
        background: {INK}; z-index: 4; }}'''
        chap_css = f'''
      .chap {{ font-family: "{F_DISPLAY}", Georgia, serif; font-weight: 400; font-style: italic;
        font-size: 30px; letter-spacing: -0.01em; color: {INK}; line-height: 1.1; }}'''

    html = f'''<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={w}, height={h}" />
    <title>{esc(title)}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family={GOOGLE}&display=swap" rel="stylesheet" />
    <script src="vendor/gsap.min.js"></script>
    <style>
      * {{ box-sizing: border-box; margin: 0; padding: 0; }}
      html, body {{ width: {w}px; height: {h}px; overflow: hidden;
        background: {PAPER}; font-family: "{F_BODY}", system-ui, sans-serif; }}
      #root {{
        position: relative; width: {w}px; height: {h}px; overflow: hidden;
        background-color: {PAPER}; color: {INK};
        background-image:
          linear-gradient(to right, {GRID} 1px, transparent 1px),
          linear-gradient(to bottom, {GRID} 1px, transparent 1px);
        background-size: 54px 54px;
        font-family: "{F_BODY}", system-ui, sans-serif;
      }}
      /* persistent animated vector backdrop */
      .bg-ring {{ position: absolute; left: 50%; top: 50%;
        width: 1500px; height: 1500px; transform: translate(-50%,-50%);
        color: {INK_FAINT}; z-index: 0; pointer-events: none; }}
{bg_dots_css}
{hairline_css}
      .hairline.top {{ top: 124px; }}
      .hairline.bottom {{ bottom: 84px; transform-origin: left; }}
      .topbar {{ position: absolute; top: 0; left: 0; right: 0; height: 108px;
        display: flex; align-items: flex-start; justify-content: space-between;
        padding: 26px 64px; z-index: 5; }}
      .tb-left, .tb-right {{ display: flex; flex-direction: column; gap: 8px; }}
      .tb-right {{ align-items: flex-end; }}
      .badge {{ font-family: '{F_MONO}', monospace; font-size: 24px; font-weight: 500;
        letter-spacing: 0.18em; text-transform: uppercase; color: {ACCENT}; }}
{chap_css}
      .cls {{ font-family: '{F_MONO}', monospace; font-size: 24px; font-weight: 500;
        letter-spacing: 0.10em; color: {INK_SOFT}; }}
      .part {{ font-family: '{F_MONO}', monospace; font-size: 22px; font-weight: 500;
        letter-spacing: 0.10em; color: {INK}; opacity: 0.45; }}
      .scene {{ position: absolute; inset: 0; display: grid; place-items: center;
        padding: 164px 80px 96px; z-index: 2; }}
{scene_inner_css}
      .image-card {{ position: relative; width: 540px; height: 540px;
        background: {PAPER}; overflow: hidden; }}
      .image-card img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
      .svg-scene-card {{ position: relative; width: 540px; height: 540px;
        overflow: hidden; }}
      .svg-scene-card svg {{ width: 100%; height: 100%; display: block; }}
      .frame, .corners {{ position: absolute; inset: 0; width: 100%; height: 100%;
        color: {INK}; pointer-events: none; }}
      .ico {{ position: relative; width: 92px; height: 92px; flex: 0 0 auto;
        color: {ACCENT}; }}
      .ico-svg {{ width: 100%; height: 100%; display: block; }}
      .orbit {{ position: absolute; left: -16px; top: -16px; width: 124px; height: 124px;
        color: {ACCENT}; opacity: 0.5; }}
      .caption {{ font-family: "{F_DISPLAY_BODY}", system-ui, sans-serif; font-weight: 500;
        font-size: 50px; line-height: 1.22; letter-spacing: -0.01em; color: {INK};
        flex: 1 1 auto; }}
      .caption-sr {{ position: absolute; width: 1px; height: 1px; overflow: hidden;
        clip: rect(0 0 0 0); }}
      .kw {{ display: inline-block; padding: 0 0.06em;
        color: {INK}; transition: none; }}
      .kw.is-active {{ color: {PAPER}; background: {ACCENT};
        box-shadow: 0 0 0 0.06em {ACCENT}; }}
      .kw.is-spoken {{ color: {ACCENT}; background: transparent; box-shadow: none; }}
{progress_css}
{glow_css}
      .pagenum {{ position: absolute; right: 64px; bottom: 104px; z-index: 6;
        font-family: "{F_MONO}", monospace; font-size: 22px; color: {INK}; opacity: 0.6; }}
      .watermark {{ position: absolute; left: 64px; bottom: 104px; z-index: 6;
        font-family: "{F_MONO}", monospace; font-size: 20px; letter-spacing: 0.12em;
        color: {INK}; opacity: 0.35; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-width="{w}" data-height="{h}" data-duration="{total}" data-fps="30">
{ambient_divs}
      <svg id="bg-ring" class="bg-ring" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <circle cx="50" cy="50" r="47" fill="none" stroke="currentColor" stroke-width="0.25" stroke-dasharray="1 4"/>
      </svg>
      <svg id="bg-dots" class="bg-dots" viewBox="0 0 100 100" preserveAspectRatio="none">
        <g fill="currentColor">
          <circle cx="8" cy="14" r="0.8"/><circle cx="92" cy="20" r="0.6"/>
          <circle cx="14" cy="86" r="0.7"/><circle cx="88" cy="82" r="0.9"/>
          <circle cx="50" cy="6" r="0.5"/><circle cx="6" cy="50" r="0.6"/>
          <circle cx="95" cy="55" r="0.5"/><circle cx="50" cy="95" r="0.7"/>
        </g>
      </svg>
      <div class="hairline top"></div>
      <div class="hairline bottom" id="hline"></div>
      <div class="topbar">
        <div class="tb-left">
          <span class="badge">{esc(subject)}</span>
          <span class="chap">{esc(title)}</span>
        </div>
        <div class="tb-right">
          <span class="cls">CLASS {esc(cls)}</span>
          <span class="part">{esc(part)}</span>
        </div>
      </div>{scenes_str}
      <div class="pagenum" id="pagenum"></div>
      <div class="watermark">NCERT SHORTS</div>
      <div class="progress" id="progress"></div>
      <audio id="narration" src="narration.wav" data-start="0" data-duration="{total}" data-track-index="0" data-volume="1"></audio>
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{tl_str}
      // persistent backdrop motion
      tl.fromTo("#bg-ring", {{ rotation: 0, transformOrigin: "50% 50%" }},
        {{ rotation: 360, transformOrigin: "50% 50%", ease: "none", duration: {total} }}, 0);
      tl.fromTo("#bg-dots", {{ y: 0 }},
        {{ y: -28, ease: "sine.inOut", duration: {max(total/2, 0.5)}, repeat: 1, yoyo: true }}, 0);
      // progress + hairline draw + page number
      tl.fromTo("#progress", {{ width: "0%" }}, {{ width: "100%", ease: "none", duration: {total} }}, 0);
      tl.fromTo("#hline", {{ scaleX: 0 }}, {{ scaleX: 1, ease: "none", duration: {total} }}, 0);
      tl.set("#pagenum", {{ textContent: "01 / {len(seq):02d}" }}, 0);
{ "".join(f'      tl.set("#pagenum", {{ textContent: "{i+1:02d} / {len(seq):02d}" }}, {round(s["start"],3)});' for i, s in enumerate(seq)) }
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>'''
    return html

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True, help="script.json (segments w/ image_prompt)")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--tts", choices=["omnivoice", "edge"], default="omnivoice")
    ap.add_argument("--voice", default="en-US-AriaNeural")
    ap.add_argument("--target-secs", type=float, default=75.0)
    ap.add_argument("--template", default=None,
                    help="template id (science/physics/maths) — overrides script.template_id")
    ap.add_argument("--svg-scenes", action="store_true",
                    help="use animated SVG inline diagrams instead of generated images")
    ap.add_argument("--diagrams", default=None,
                    help="path to JSON file mapping segment index -> {path, description} for textbook diagram overlay")
    args = ap.parse_args()

    script = json.load(open(args.script, encoding="utf-8"))
    segs = script["segments"]
    title = script.get("title", "Untitled")
    subject = script.get("subject", "")
    cls = script.get("class", "")
    template_id = args.template or script.get("template_id") or "science"
    theme = load_theme(template_id)

    out_dir = os.path.join(ROOT, "pipeline", "rendered", args.slug)
    seg_dir = os.path.join(out_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "vendor"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "pipeline", "vendor", "gsap.min.js"),
                os.path.join(out_dir, "vendor", "gsap.min.js"))

    # image manifest from gen_images.py
    img_manifest = {}
    imp = os.path.join(out_dir, "images", "manifest.json")
    if os.path.exists(imp):
        try:
            img_manifest = json.load(open(imp))
        except Exception:
            img_manifest = {}

    def img_for(i):
        rel = f"images/seg_{i:02d}.png"
        full = os.path.join(out_dir, rel)
        if os.path.exists(full) and os.path.getsize(full) > 2000:
            return rel
        e = img_manifest.get(str(i)) or img_manifest.get(i)
        return e["file"] if e and e.get("file") else None

    # Build full script text for single-block TTS
    full_text = "\n\n".join(s.get("text", "").strip() for s in segs if s.get("text", "").strip())
    full_wav = os.path.join(out_dir, "narration_full.wav")

    if args.tts == "omnivoice":
        try:
            tts_omnivoice(full_text, full_wav)
        except Exception as e:
            print(f"[warn] omnivoice failed ({e}), falling back to edge-tts", file=sys.stderr)
            args.tts = "edge"

    if args.tts != "omnivoice":
        full_text_joined = " ".join(s.get("text", "").strip() for s in segs if s.get("text", "").strip())
        tts_edge(full_text_joined, full_wav, args.voice)

    full_dur = probe_duration(full_wav)
    # Copy full narration as the master audio file
    shutil.copy(full_wav, os.path.join(out_dir, "narration.wav"))

    # Split full audio into segments by proportional timing
    total_chars = sum(len(s.get("text", "").strip()) for s in segs if s.get("text", "").strip())
    seg_times = []
    char_acc = 0
    for s in segs:
        tlen = len(s.get("text", "").strip())
        if tlen == 0:
            seg_times.append(0)
            continue
        seg_times.append(full_dur * tlen / total_chars)
        char_acc += tlen

    # Extract per-segment WAVs from the single full recording
    seq, t, chosen = [], 0.0, 0
    for i, s in enumerate(segs):
        text = s.get("text", "").strip()
        if not text:
            continue
        dur = seg_times[i]
        wav = os.path.join(seg_dir, f"seg_{i:02d}.wav")
        # Extract segment audio from full recording using ffmpeg
        end = t + dur
        subprocess.run([
            "ffmpeg", "-y", "-i", full_wav,
            "-ss", str(t), "-to", str(end),
            "-c", "copy", wav,
        ], capture_output=True)
        seq.append({
            "index": i, "text": text, "start": round(t, 3),
            "duration": round(dur, 3), "image": img_for(i),
            "icon": icon_for_segment(s, subject),
        })
        t = end
        chosen += 1
        if t >= args.target_secs:
            break

    if not seq:
        print("[error] no segments produced audio", file=sys.stderr)
        sys.exit(2)

    # word timing via Groq Whisper (verbose_json + word timestamps)
    whisper_words = None
    concat = os.path.join(out_dir, "narration.wav")
    segs_files = [os.path.join(out_dir, "segments", f"seg_{s['index']:02d}.wav")
                  for s in seq]
    cl = os.path.join(out_dir, "_concat.txt")
    with open(cl, "w") as f:
        for p in segs_files:
            f.write(f"file '{p}'\n")
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", cl, "-c", "copy", concat],
                       capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(concat):
        whisper_words = transcribe_groq(concat)
        if whisper_words:
            print(f"[align] groq whisper: {len(whisper_words)} word timestamps "
                  f"across {len(seq)} segments", file=sys.stderr)
        else:
            print("[align] whisper unavailable — even-split karaoke", file=sys.stderr)
    else:
        print("[align] concat failed — even-split karaoke", file=sys.stderr)

    part = script.get("part", "")
    diagrams_map = {}
    if args.diagrams and os.path.exists(args.diagrams):
        try:
            diagrams_map = json.load(open(args.diagrams))
            print(f"[diagrams] loaded {len(diagrams_map)} diagram references", file=sys.stderr)
        except Exception as e:
            print(f"[diagrams] failed to load: {e}", file=sys.stderr)

    html = build_html(seq, title, subject, cls, args.slug,
                      whisper_words=whisper_words, part=part, theme=theme,
                      svg_mode=args.svg_scenes, diagrams=diagrams_map)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)

    manifest = {
        "title": title, "subject": subject, "class": cls, "tts": args.tts,
        "total": round(t, 3), "segments": seq,
        "word_alignment": "groq-whisper" if whisper_words else "even-split",
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps({"slug": args.slug, "dir": out_dir, "segments": chosen,
                      "total_seconds": round(t, 3),
                      "word_alignment": "groq-whisper" if whisper_words else "even-split"}))

if __name__ == "__main__":
    sys.exit(main())
