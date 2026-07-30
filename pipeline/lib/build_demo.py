#!/usr/bin/env python3
"""
build_demo.py v2 — Full animation demo builder.
Each scene = photorealistic image (background) + animation components (pills/stats/think-box/slam) + lower-third karaoke captions.
Animation patterns match the hf-demo exactly: elastic bounce, pill stagger back.out(3), stat card fly-in, think box slide, number slam.
"""

import argparse, json, os, re, subprocess, sys, shutil, urllib.request, urllib.error, difflib, random

ROOT = "/home/fiipadmin/projects/cbse-youtube-channel"
VENV = "/home/fiipadmin/.hermes/hermes-agent/venv"
PY = os.path.join(VENV, "bin", "python")
EDGE_TTS = os.path.join(VENV, "bin", "edge-tts")

CB_SERVER = "http://127.0.0.1:8004"
CB_VOICE = "Rick.wav"
CB_MODEL = "Rick.wav"

PATTERNS = ["hook", "keyword", "stat", "think", "finale"]

def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

# ── TTS ────────────────────────────────────────────────────────────────────────
def tts_chatterbox(text, out_wav):
    payload = json.dumps({"model": CB_MODEL, "input": text, "voice": CB_VOICE, "response_format": "wav"}).encode()
    req = urllib.request.Request(CB_SERVER + "/v1/audio/speech", data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if not data or len(data) < 1000:
        raise RuntimeError("chatterbox short audio")
    with open(out_wav, "wb") as f:
        f.write(data)

def tts_edge(text, out_wav, voice="en-US-AndrewNeural"):
    mp3 = out_wav[:-4] + ".mp3"
    subprocess.run([EDGE_TTS, "--voice", voice, "--text", text, "--write-media", mp3], capture_output=True, check=True)
    subprocess.run(["ffmpeg", "-y", "-i", mp3, out_wav], capture_output=True, check=True)
    os.remove(mp3)

def probe_duration(wav):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", wav], capture_output=True, text=True)
    return float(r.stdout.strip())

# ── Whisper ────────────────────────────────────────────────────────────────────
def _groq_key():
    try:
        from hermes_cli.config import get_env_value
        k = get_env_value("GROQ_API_KEY")
        if k: return k
    except Exception: pass
    return os.getenv("GROQ_API_KEY")

def transcribe_groq(wav_path):
    try:
        from openai import OpenAI
    except ImportError: return None
    key = _groq_key()
    if not key: return None
    try:
        client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1", timeout=120, max_retries=1)
        with open(wav_path, "rb") as f:
            r = client.audio.transcriptions.create(model="whisper-large-v3-turbo", file=f, response_format="verbose_json", timestamp_granularities=["word"], language="en")
        d = r.model_dump() if hasattr(r, "model_dump") else json.loads(r.model_dump_json())
        return d.get("words") or []
    except Exception as e:
        print(f"[warn] whisper: {e}", file=sys.stderr)
        return None

def normalize_token(tok):
    return re.sub(r"[^a-z0-9]", "", tok.lower())

def align_words(script_words, whisper_words):
    if not script_words or not whisper_words: return None
    s_norm = [normalize_token(w) for w in script_words]
    w_norm = [normalize_token(w.get("word","")) for w in whisper_words]
    sm = difflib.SequenceMatcher(a=s_norm, b=w_norm, autojunk=False)
    out = [None] * len(script_words)
    prev_end = whisper_words[0]["start"]
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                w = whisper_words[j1 + (k - i1)]
                out[k] = (w["start"], w["end"])
        elif tag == "replace":
            wb = whisper_words[j1:j2]
            if wb:
                ts, te = wb[0]["start"], wb[-1]["end"]
                for k in range(i1, i2): out[k] = (ts, te)
    if any(o is None for o in out): return None
    return out

def align_segment_times(abs_start, dur, script_words, whisper_words):
    n = len(script_words)
    if n == 0: return []
    if not whisper_words:
        return [(abs_start + (k/n)*dur, abs_start + ((k+1)/n)*dur) for k in range(n)]
    w_in = [w for w in whisper_words if abs_start <= w["start"] < abs_start + dur + 0.001]
    if not w_in:
        return [(abs_start + (k/n)*dur, abs_start + ((k+1)/n)*dur) for k in range(n)]
    aligned = align_words(script_words, w_in)
    if aligned is None:
        t0, t1 = w_in[0]["start"], w_in[-1]["end"]
        return [(t0 + (t1-t0)*(k/n), t0 + (t1-t0)*((k+1)/n)) for k in range(n)]
    return aligned

# ── Keyword extraction ──────────────────────────────────────────────────────────
STOPWORDS = set("the a an and or of in to for is at on it by with from that this are was were be has have do does not no but its".split())

def extract_keywords(text, max_kw=5):
    """Extract 3-5 important keyword pills from segment text."""
    # Clean markers
    clean = re.sub(r"\[[A-Z ]+\]\s*", "", text)
    # Split words
    words = re.findall(r"[A-Za-z]+", clean)
    # Score: prefer longer words, capitalized, not stopwords
    scored = []
    for w in words:
        wl = w.lower()
        if wl in STOPWORDS or len(wl) < 4: continue
        score = len(wl) * 1.0
        if w[0].isupper(): score += 2  # proper nouns
        if any(c.isupper() for c in w): score += 1  # mixed case
        scored.append((score, w.capitalize()))
    scored.sort(reverse=True)
    # Deduplicate and pick top
    seen = set()
    result = []
    for _, w in scored:
        if w.lower() not in seen and len(result) < max_kw:
            seen.add(w.lower())
            result.append(w)
    if not result:
        # Fallback: first 3 non-stopwords
        for w in words:
            if w.lower() not in STOPWORDS and len(result) < max_kw:
                result.append(w.capitalize())
    # Pad if needed
    while len(result) < 3 and len(words) > len(result):
        result.append(words[len(result)].capitalize())
    return result[:5]

def extract_number(text):
    """Extract a number from text for stat/finale scenes."""
    nums = re.findall(r"\d[\d,]*", text)
    if nums:
        try: return int(nums[0].replace(",",""))
        except: pass
    return None

# ── HTML builder ────────────────────────────────────────────────────────────────
def build_html(segments, title, subject, cls, part, whisper_words=None):
    total = round(max(s["end"] for s in segments), 3) if segments else 120
    scenes_html = []
    timeline = []
    anim_css = []
    n = len(segments)

    for i, s in enumerate(segments):
        idx = s["index"]
        start = round(s["start"], 3)
        dur = round(s["duration"], 3)
        img = s.get("image")
        text = s["text"]
        pattern = PATTERNS[i % len(PATTERNS)]
        
        # Clean text for display
        tag_match = re.match(r"^\[([A-Z ]+)\]\s*(.*)", text)
        scene_tag = tag_match.group(1).strip() if tag_match else ""
        display_text = tag_match.group(2) if tag_match else text

        # Word timestamps for karaoke
        words = re.findall(r"\S+", display_text)
        word_times = align_segment_times(start, dur, words, whisper_words or [])
        spans = []
        for wi, wd in enumerate(words):
            ws, we = word_times[wi] if wi < len(word_times) else (start, start+dur)
            spans.append((f"w-{i}-{wi}", wd, round(ws,3), round(we,3)))
        word_html = "".join(f'<span class="kw" id="{eid}">{esc(wd)}</span>' for eid, wd, _, _ in spans)

        # Keywords for pills
        keywords = extract_keywords(display_text)
        kw_count = len(keywords)
        
        # Number for stat/finale
        number = extract_number(display_text)

        # ── Scene HTML ──
        # Glassmorphic image card instead of blurred background
        img_card = f'''<div class="image-card" id="ic-{i}">
            <img src="{esc(img)}" alt="" />
            <div class="ic-border"></div>
          </div>''' if img else ''

        # Create all the animation elements
        anim_parts = []
        
        # Tag label (ACTIVITY, THINK IT OVER, DID YOU KNOW)
        if scene_tag:
            anim_parts.append(f'<div class="scene-tag" id="tag-{i}">{esc(scene_tag)}</div>')
        
        if pattern == "hook":
            # Hero text with elastic bounce + subtitle
            lines = display_text.split("?") if "?" in display_text else [display_text[:60], display_text[60:120]]
            hero = lines[0] + ("?" if display_text.count("?") > 0 else "")
            sub = lines[1] if len(lines) > 1 else ""
            anim_parts.append(f'<div class="hero-text" id="hero-{i}">{esc(hero)}</div>')
            if sub.strip():
                anim_parts.append(f'<div class="hero-sub" id="sub-{i}">{esc(sub.strip())}</div>')
        
        elif pattern == "keyword":
            # Pill stagger like demo
            tag_text = scene_tag or "Key Concepts"
            anim_parts.append(f'<div class="kw-tag" id="kwt-{i}">{esc(tag_text)}</div>')
            pills = ""
            for ki, kw in enumerate(keywords):
                pills += f'<span class="pill" id="p{i}-{ki}">{esc(kw)}</span>'
            anim_parts.append(f'<div class="pill-row" id="pr-{i}">{pills}</div>')
        
        elif pattern == "stat":
            # Stat cards like demo
            tag_text = scene_tag or "Key Quantities"
            anim_parts.append(f'<div class="stat-tag" id="stt-{i}">{esc(tag_text)}</div>')
            cards = ""
            if number:
                cards += f'<div class="stat-card" id="sc{i}-0"><div class="stat-num">{number}</div><div class="stat-label">{esc(keywords[0] if keywords else "Total")}</div><div class="stat-unit">unit</div></div>'
            for ki, kw in enumerate(keywords[:3]):
                cards += f'<div class="stat-card" id="sc{i}-{ki+1}"><div class="stat-num">{ki+1}</div><div class="stat-label">{esc(kw)}</div></div>'
            anim_parts.append(f'<div class="stat-row" id="sr-{i}">{cards}</div>')
        
        elif pattern == "think":
            # Think It Over — question + answer box
            tag_text = scene_tag or "Think It Over"
            anim_parts.append(f'<div class="think-tag" id="tht-{i}">{esc(tag_text)}</div>')
            # Split at question mark for Q&A
            parts = display_text.split("?")
            question = parts[0] + "?" if len(parts) > 1 else display_text[:80] + "?"
            answer = parts[1].strip() if len(parts) > 1 else ""
            anim_parts.append(f'<div class="think-q" id="thq-{i}">{esc(question)}</div>')
            if answer:
                anim_parts.append(f'<div class="think-a" id="tha-{i}"><div class="think-a-label">Answer</div><div class="think-a-text">{esc(answer)}</div></div>')
        
        elif pattern == "finale":
            # Big number slam + summary
            final_num = str(number) if number else str(len(keywords))
            anim_parts.append(f'<div class="final-num" id="fin-{i}"><span class="gradient-text">{esc(final_num)}</span></div>')
            anim_parts.append(f'<div class="final-label" id="finl-{i}">{esc(keywords[0] if keywords else "Key")}</div>')
            rest = [esc(k) for k in keywords[1:4]]
            if rest:
                anim_parts.append(f'<div class="final-meta" id="finm-{i}">{esc(" · ".join(rest))}</div>')

        # Combine all animation parts into one stack
        anim_stack = "\n          ".join(anim_parts)

        scene = f'''
        <section class="clip" id="s{i}" data-start="{start}" data-duration="{dur}">
          <div class="scene-content" id="sc-{i}">
            {img_card}
            {anim_stack}
          </div>
          <div class="caption-bar" id="cbar-{i}">
            <div class="caption-text">{word_html}</div>
          </div>
        </section>'''
        scenes_html.append(scene)

        # ── GSAP timeline ──
        d0 = start + 0.05  # first timing tick

        # Image card entrance (glassmorphic card)
        if img:
            timeline.append(f'      tl.from("#ic-{i}",{{scale:0.9,opacity:0,rotation:-3,duration:0.6,ease:"power3.out"}},{d0});')

        if pattern == "hook":
            timeline.append(f'      tl.from("#tag-{i}",{{y:-12,opacity:0,duration:0.25,ease:"power2.out"}},{d0});') if scene_tag else None
            timeline.append(f'      tl.from("#hero-{i}",{{y:60,scale:0.85,opacity:0,duration:0.7,ease:"elastic.out(1,0.45)"}},{d0}+0.1);')
            timeline.append(f'      tl.from("#sub-{i}",{{y:20,opacity:0,duration:0.35,ease:"power2.out"}},{d0}+0.7);') if "sub-" in str(anim_parts) else None
        
        elif pattern == "keyword":
            timeline.append(f'      tl.from("#kwt-{i}",{{y:-12,opacity:0,duration:0.25,ease:"power2.out"}},{d0});')
            for ki in range(kw_count):
                delay = d0 + 0.2 + ki * 0.08
                timeline.append(f'      tl.from("#p{i}-{ki}",{{scale:0,opacity:0,rotation:-15,duration:0.45,ease:"back.out(3)"}},{delay});')
        
        elif pattern == "stat":
            timeline.append(f'      tl.from("#stt-{i}",{{y:-12,opacity:0,duration:0.25,ease:"power2.out"}},{d0});')
            card_positions = [("x", -80), ("y", 60), ("x", 80), ("y", -60)]
            for ki in range(min(len(keywords)+1, 4)):
                pos = card_positions[ki % len(card_positions)]
                delay = d0 + 0.2 + ki * 0.18
                timeline.append(f'      tl.from("#sc{i}-{ki}",{{{pos[0]}:{pos[1]},opacity:0,duration:0.5,ease:"power3.out"}},{delay});')
        
        elif pattern == "think":
            timeline.append(f'      tl.from("#tht-{i}",{{y:-12,opacity:0,duration:0.25,ease:"power2.out"}},{d0});')
            timeline.append(f'      tl.from("#thq-{i}",{{y:24,opacity:0,duration:0.35,ease:"power2.out"}},{d0}+0.15);')
            timeline.append(f'      tl.from("#tha-{i}",{{x:-60,opacity:0,duration:0.55,ease:"power3.out"}},{d0}+0.45);')
        
        elif pattern == "finale":
            timeline.append(f'      tl.from("#fin-{i}",{{scale:4,opacity:0,rotation:12,duration:0.7,ease:"elastic.out(1,0.35)"}},{d0});')
            timeline.append(f'      tl.from("#finl-{i}",{{y:24,opacity:0,duration:0.4,ease:"power2.out"}},{d0}+0.6);') if i == n-1 or True else None
            timeline.append(f'      tl.from("#finm-{i}",{{y:12,opacity:0,duration:0.35,ease:"power2.out"}},{d0}+0.9);') if i == n-1 or True else None

        # Caption bar
        timeline.append(f'      tl.from("#cbar-{i}",{{y:60,opacity:0,duration:0.4,ease:"power3.out"}},{d0}+0.2);')

        # Word karaoke
        for eid, wd, ws, we in spans:
            timeline.append(f'      tl.set("#{eid}",{{className:"kw is-active"}},{ws});')
            timeline.append(f'      tl.set("#{eid}",{{className:"kw is-spoken"}},{we});')
            timeline.append(f'      tl.fromTo("#{eid}",{{scale:0.96}},{{scale:1,duration:0.1,ease:"power3.out"}},{ws});')

        # Exit
        if i + 1 < n:
            nx = round(segments[i+1]["start"], 3)
            timeline.append(f'      tl.to("#sc-{i}",{{opacity:0,y:-15,duration:0.25,ease:"power2.in"}},{nx}-0.25);')
            timeline.append(f'      tl.to("#cbar-{i}",{{opacity:0,y:30,duration:0.2,ease:"power2.in"}},{nx}-0.2);')
            timeline.append(f'      tl.set("#s{i}",{{opacity:0}},{nx});')

    scenes_str = "\n".join(scenes_html)
    tl_str = "\n".join(timeline)

    # Background geometric pattern — layered mesh + particles
    bg_pattern = "".join(
        f'<polygon points="{x},{y+29} {x+25},{y} {x+50},{y+29} {x+25},{y+58}" fill="none" stroke="rgba(255,255,255,0.02)" stroke-width="1"/>'
        for x in range(0, 1081, 50)
        for y in range(0, 1921, 58)
    ) + "".join(
        f'<line x1="{x}" y1="0" x2="{x}" y2="1920" stroke="rgba(255,255,255,0.012)" stroke-width="0.5"/>'
        for x in range(0, 1081, 25)
    ) + "".join(
        f'<circle cx="{x}" cy="{y}" r="{1.5 if (x+y)%200==0 else 1}" fill="rgba(77,143,247,{0.06 if (x+y)%200==0 else 0.03})"/>'
        for x in range(0, 1081, 60)
        for y in range(0, 1921, 60)
    ) + "".join(
        f'<path d="M{x} {y} Q{x+20} {y-20} {x+40} {y}" fill="none" stroke="rgba(122,196,245,0.015)" stroke-width="1"/>'
        for x in range(0, 1081, 120)
        for y in range(0, 1921, 120)
    )

    html = f'''<!doctype html>
<html lang="en">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=1080,height=1920"/>
<script src="vendor/gsap.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:1080px;height:1920px;overflow:hidden;background:#0E1420;font-family:'Inter',sans-serif;color:#E8E9F0}}
#root{{position:relative;width:1080px;height:1920px;overflow:hidden;z-index:0}}

/* ── Background ── */
.bg-glows{{position:absolute;inset:-200px;
  background:radial-gradient(ellipse at 30% 20%,rgba(77,143,247,0.08),transparent 60%),
             radial-gradient(ellipse at 70% 80%,rgba(139,127,232,0.06),transparent 55%);
  pointer-events:none;z-index:0}}
.bg-pattern{{position:absolute;inset:0;pointer-events:none;z-index:0;opacity:0.5}}
.bg-grain{{position:absolute;inset:0;pointer-events:none;z-index:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
  opacity:0.5}}

/* ── Top bar ── */
.topbar{{position:absolute;top:0;left:0;right:0;height:100px;padding:24px 50px;
  display:flex;justify-content:space-between;align-items:flex-start;z-index:10}}
.badge{{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:500;
  letter-spacing:3px;text-transform:uppercase;color:#4D8FF7}}
.chap-title{{font-family:'Inter',sans-serif;font-size:26px;font-weight:400;
  color:#9A9BA3;margin-top:4px;letter-spacing:-0.3px}}
.cls-tag{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:500;letter-spacing:2px;color:#6B7280}}
.part-tag{{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:400;letter-spacing:2px;color:#6B7280;opacity:0.6;text-align:right}}

/* ── Clips ── */
.clip{{position:absolute;inset:0;z-index:2;overflow:hidden}}

/* ── Glassmorphic image card ── */
.image-card{{position:relative;width:580px;height:580px;border-radius:24px;overflow:hidden;
  background:rgba(255,255,255,0.04);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border:1px solid rgba(255,255,255,0.12);flex-shrink:0;
  box-shadow:0 12px 48px rgba(0,0,0,0.35),inset 0 1px 0 rgba(255,255,255,0.08);
  margin-bottom:16px}}
.image-card img{{width:100%;height:100%;object-fit:cover;display:block;border-radius:24px}}
.ic-border{{position:absolute;inset:2px;border-radius:22px;
  border:1.5px solid rgba(255,255,255,0.06);pointer-events:none}}

/* ── Scene content (animation zone) ── */
.scene-content{{position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;padding:120px 60px 260px;z-index:3}}
.scene-tag{{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:500;
  letter-spacing:4px;text-transform:uppercase;color:#7AC4F5;margin-bottom:20px}}

/* Hook pattern */
.hero-text{{font-family:'Inter',sans-serif;font-weight:900;font-size:72px;line-height:1.15;
  text-align:center;max-width:920px;filter:drop-shadow(0 0 60px rgba(77,143,247,0.2));
  color:#FFFFFF;margin-bottom:12px}}
.hero-sub{{font-family:'Inter',sans-serif;font-weight:400;font-size:32px;
  color:#9A9BA3;text-align:center;max-width:800px;letter-spacing:-0.3px}}

/* Keyword pill pattern */
.kw-tag{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:500;
  letter-spacing:3px;text-transform:uppercase;color:#7AC4F5;margin-bottom:28px;text-align:center}}
.pill-row{{text-align:center}}
.pill{{display:inline-block;padding:16px 36px;margin:8px;border-radius:999px;
  background:rgba(255,255,255,0.04);border:1px solid rgba(77,143,247,0.2);
  font-size:40px;font-weight:600;letter-spacing:-0.5px;color:#FFFFFF;
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  box-shadow:0 8px 32px rgba(0,0,0,0.3),inset 0 1px 0 rgba(255,255,255,0.06)}}

/* Stat card pattern */
.stat-tag{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:500;
  letter-spacing:3px;text-transform:uppercase;color:#4D8FF7;margin-bottom:20px}}
.stat-row{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}}
.stat-card{{width:220px;padding:28px 16px 24px;border-radius:24px;
  background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);
  text-align:center;position:relative;overflow:hidden;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  box-shadow:0 8px 32px rgba(0,0,0,0.2),inset 0 1px 0 rgba(255,255,255,0.06)}}
.stat-card::before{{content:'';position:absolute;top:0;left:0;width:100%;height:3px;
  background:linear-gradient(90deg,#4D8FF7,#7AC4F5);transform:scaleX(0);transform-origin:left}}
.stat-num{{font-size:56px;font-weight:800;
  background:linear-gradient(135deg,#4D8FF7,#7AC4F5);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:4px}}
.stat-label{{font-size:20px;color:#C8CCD4;font-weight:500}}
.stat-unit{{font-family:'JetBrains Mono',monospace;font-size:14px;color:#6B7280;margin-top:8px;letter-spacing:2px}}

/* Think It Over */
.think-tag{{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:500;
  letter-spacing:3px;text-transform:uppercase;color:#7AC4F5;margin-bottom:16px}}
.think-q{{font-size:36px;text-align:center;color:#E8E9F0;max-width:800px;margin-bottom:16px;font-weight:500}}
.think-a{{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.10);
  border-radius:20px;padding:24px 28px;max-width:820px;position:relative;overflow:hidden;
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  box-shadow:0 8px 32px rgba(0,0,0,0.2),inset 0 1px 0 rgba(255,255,255,0.06)}}
.think-a::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
  background:linear-gradient(180deg,#4D8FF7,#7AC4F5)}}
.think-a-label{{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:500;
  color:#4D8FF7;letter-spacing:2px;margin-bottom:8px}}
.think-a-text{{font-size:28px;line-height:1.4;color:#C8CCD4;font-weight:400}}

/* Finale */
.final-num{{font-size:150px;font-weight:900;letter-spacing:-6px;text-align:center;
  line-height:1;filter:drop-shadow(0 0 80px rgba(77,143,247,0.25))}}
.gradient-text{{background:linear-gradient(135deg,#4D8FF7,#7AC4F5,#8B7FE8);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.final-label{{font-size:32px;color:#E8E9F0;text-align:center;margin-top:8px;font-weight:500;letter-spacing:-0.3px}}
.final-meta{{font-family:'JetBrains Mono',monospace;font-size:20px;color:#6B7280;text-align:center;margin-top:16px;letter-spacing:3px}}

/* ── Lower third caption bar ── */
.caption-bar{{position:absolute;bottom:40px;left:40px;right:40px;
  background:rgba(14,20,32,0.70);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border:1px solid rgba(255,255,255,0.10);border-radius:20px;padding:20px 28px;z-index:5;
  max-height:180px;overflow:hidden}}
.caption-text{{font-family:'Inter',sans-serif;font-size:34px;line-height:1.35;
  font-weight:500;text-align:center;color:#FFFFFF;word-wrap:break-word}}
.kw{{display:inline-block;padding:0 0.04em;color:#C8CCD4;opacity:0.7;transition:none}}
.kw.is-active{{color:#FFFFFF;opacity:1;text-shadow:0 0 24px rgba(77,143,247,0.4)}}
.kw.is-spoken{{color:#9A9BA3;opacity:0.5}}

/* ── Progress ── */
.progress{{position:absolute;bottom:0;left:0;height:4px;width:0%;
  background:linear-gradient(90deg,#4D8FF7,#7AC4F5);z-index:10}}

/* ── Particle float animation ── */
@keyframes float{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-24px)}}}}
.p-f{{animation:float 10s ease-in-out infinite}}

audio{{display:none}}
</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="1080" data-height="1920">
  <div class="bg-glows"></div>
  <svg class="bg-pattern">{bg_pattern}</svg>
  <div class="bg-grain"></div>

  <div class="topbar">
    <div><div class="badge">{esc(subject)}</div><div class="chap-title">{esc(title)}</div></div>
    <div><div class="cls-tag">CLASS {esc(cls)}</div><div class="part-tag">{esc(part)}</div></div>
  </div>

  {scenes_str}

  <div class="progress" id="progress"></div>
</div>

<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{paused:true}});

{tl_str}

// Persistent bg
tl.fromTo("#progress",{{width:"0%"}},{{width:"100%",ease:"none",duration:{total}}},0);

window.__timelines["main"] = tl;
</script>
</body>
</html>'''
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--tts", choices=["chatterbox","edge"], default="chatterbox")
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    args = ap.parse_args()

    out_dir = os.path.join(ROOT, "pipeline", "rendered", args.slug)
    os.makedirs(os.path.join(out_dir, "vendor"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "pipeline", "vendor", "gsap.min.js"),
                os.path.join(out_dir, "vendor", "gsap.min.js"))

    script = json.load(open(os.path.join(out_dir, "script.json")))
    segs = script["segments"]
    title = script.get("title","Untitled")
    subject = script.get("subject","")
    cls = script.get("class","")
    part = script.get("part","")

    img_manifest = {}
    imp = os.path.join(out_dir, "images", "manifest.json")
    if os.path.exists(imp):
        try: img_manifest = json.load(open(imp))
        except: img_manifest = {}

    # ── TTS per segment ──
    seg_dir = os.path.join(out_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    total_audio = []
    for i, s in enumerate(segs):
        text = re.sub(r"\[[A-Z ]+\]\s*","", s["text"]).strip()
        wav = os.path.join(seg_dir, f"seg_{i:02d}.wav")
        if not os.path.exists(wav) or os.path.getsize(wav) < 2000:
            print(f"[TTS] seg {i}...", end=" ", flush=True)
            if args.tts == "chatterbox": tts_chatterbox(text, wav)
            else: tts_edge(text, wav, args.voice)
            print(f"{os.path.getsize(wav)} bytes")
        dur = probe_duration(wav)
        total_audio.append((i, wav, dur))
        s["duration"] = round(dur, 3)

    # ── Concatenate ──
    n_wav = os.path.join(out_dir, "narration.wav")
    concat_txt = os.path.join(out_dir, "_concat.txt")
    with open(concat_txt, "w") as f:
        for _, wav, _ in total_audio:
            f.write(f"file '{os.path.abspath(wav)}'\n")
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",concat_txt,"-c","copy",n_wav], capture_output=True, check=True)
    total_dur = probe_duration(n_wav)
    print(f"[audio] Total: {total_dur:.2f}s ({len(total_audio)} segments)")

    # ── Whisper ──
    whisper_words = transcribe_groq(n_wav)
    if whisper_words: print(f"[whisper] {len(whisper_words)} word timestamps")

    # ── Assign timing + images ──
    running = 0.0
    for s in segs:
        s["start"] = round(running, 3)
        s["end"] = round(running + s["duration"], 3)
        idx = s["index"]
        rel = f"images/seg_{idx:02d}.png"
        full = os.path.join(out_dir, rel)
        if os.path.exists(full) and os.path.getsize(full) > 2000:
            s["image"] = rel
        else:
            e = img_manifest.get(str(idx)) or img_manifest.get(idx)
            s["image"] = e["file"] if e and e.get("file") else None
        running += s["duration"]

    # ── Build HTML ──
    html = build_html(segs, title, subject, cls, part, whisper_words)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # ── Meta ──
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump({"total": round(total_dur,3), "segments": len(segs)}, f)
    with open(os.path.join(out_dir, "hyperframes.json"), "w") as f:
        json.dump({"composition":"main","audio":"narration.wav"}, f)

    print(f"[done] {out_dir}/index.html ({total_dur:.2f}s, {len(segs)} segments)")
    print(json.dumps({"slug":args.slug,"duration":round(total_dur,3),"segments":len(segs),"words":len(whisper_words or [])}))

if __name__ == "__main__":
    sys.exit(main())