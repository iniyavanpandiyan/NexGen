#!/usr/bin/env python3
"""build_demo_v4.py — Tight 40s demo with hand-crafted animations matching each segment.
No images needed — the animations ARE the visual. Graph-paper grid background.
"""

import argparse, json, os, re, subprocess, sys, shutil, urllib.request, urllib.error, difflib

ROOT = "/home/fiipadmin/workspace/NexGen"
VENV = "/home/fiipadmin/.hermes/hermes-agent/venv"
EDGE_TTS = os.path.join(VENV, "bin", "edge-tts")

def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

def tts_edge(text, out_wav, voice="en-US-AndrewNeural"):
    mp3 = out_wav[:-4] + ".mp3"
    subprocess.run([EDGE_TTS, "--voice", voice, "--text", text, "--write-media", mp3], capture_output=True, check=True)
    subprocess.run(["ffmpeg", "-y", "-i", mp3, out_wav], capture_output=True, check=True)
    os.remove(mp3)

def probe_duration(wav):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", wav], capture_output=True, text=True)
    return float(r.stdout.strip())

def build_html(segments, title, subject, cls, part):
    total = round(max(s["end"] for s in segments), 3) if segments else 40
    scenes_html = []
    timeline = []
    n = len(segments)

    # Graph-paper grid SVG — dense, detailed
    grid_lines = ""
    for x in range(0, 1081, 20):
        w = "0.3" if x % 100 == 0 else "0.15"
        o = "0.06" if x % 100 == 0 else "0.03"
        grid_lines += f'<line x1="{x}" y1="0" x2="{x}" y2="1920" stroke="rgba(77,143,247,{o})" stroke-width="{w}"/>'
    for y in range(0, 1921, 20):
        w = "0.3" if y % 100 == 0 else "0.15"
        o = "0.06" if y % 100 == 0 else "0.03"
        grid_lines += f'<line x1="0" y1="{y}" x2="1080" y2="{y}" stroke="rgba(77,143,247,{o})" stroke-width="{w}"/>'

    for i, s in enumerate(segments):
        start = round(s["start"], 3)
        dur = round(s["duration"], 3)
        text = s["text"]
        d0 = start + 0.05

        # Each segment has a hand-crafted scene type
        scene_type = [
            "hook",     # 0 — Hook: how do scientists understand?
            "pill",     # 1 — They build models: pill stagger
            "think",    # 2 — Do we need brand/colour? Think it over
            "stat",     # 3 — Three things matter: stat cards
            "dismiss",  # 4 — Small effects, ignore: dismiss animation
            "activity", # 5 — Now you try: activity reveal
            "pill",     # 6 — What matters vs doesn't: pill split
            "finale"    # 7 — Conclusion: number slam
        ][i]

        # ── Scene HTML ──
        anim_parts = []

        if scene_type == "hook":
            anim_parts.append(f'<div class="tag" id="t{i}">Science · Class 9</div>')
            anim_parts.append(f'<div class="hero" id="h{i}">How do scientists understand<br><span class="g">complex things</span>?</div>')
            anim_parts.append(f'<div class="sub" id="s{i}">Like a cricket ball flying for a six.</div>')

        elif scene_type == "pill":
            if i == 1:
                anim_parts.append(f'<div class="tag" id="t{i}">💡 The Answer</div>')
                anim_parts.append(f'<div class="pill-row" id="pr{i}">' +
                    '<span class="pill" id="p1-0">Models</span>' +
                    '<span class="pill" id="p1-1">Simplified</span>' +
                    '<span class="pill" id="p1-2">Reality</span>' +
                    '<span class="pill" id="p1-3">Focus</span>' +
                    '</div>')
                anim_parts.append(f'<div class="sub" id="s{i}">Only what matters for your question</div>')
            else:
                anim_parts.append(f'<div class="tag" id="t{i}">⚖️ What Matters vs What Doesn\'t</div>')
                anim_parts.append(f'<div class="pill-row" id="pr{i}">' +
                    '<span class="pill green" id="p2-0">Speed</span>' +
                    '<span class="pill green" id="p2-1">Distance</span>' +
                    '<span class="pill green" id="p2-2">Traffic Lights</span>' +
                    '</div>')
                anim_parts.append(f'<div class="pill-row" id="pr{i}b" style="margin-top:16px">' +
                    '<span class="pill dim" id="p2-3">Shirt Colour</span>' +
                    '<span class="pill dim" id="p2-4">Bike Brand</span>' +
                    '</div>')

        elif scene_type == "think":
            anim_parts.append(f'<div class="tag" id="t{i}">🤔 Think It Over</div>')
            anim_parts.append(f'<div class="think-q" id="q{i}">Do we need the brand of the bat?<br>Or the colour of the ball?</div>')
            anim_parts.append(f'<div class="think-a" id="a{i}"><div class="tal">❝ Answer</div><div class="tatx">Of course not! A good model ignores irrelevant details.</div></div>')

        elif scene_type == "stat":
            anim_parts.append(f'<div class="tag" id="t{i}">📊 The Only Things That Matter</div>')
            anim_parts.append(f'<div class="stat-row" id="sr{i}">' +
                '<div class="stat-card" id="sc0"><div class="stat-num">Mass</div><div class="stat-label">Of the ball</div><div class="stat-unit">m</div></div>' +
                '<div class="stat-card" id="sc1"><div class="stat-num">Speed</div><div class="stat-label">Of the hit</div><div class="stat-unit">v</div></div>' +
                '<div class="stat-card" id="sc2"><div class="stat-num">Angle</div><div class="stat-label">Of launch</div><div class="stat-unit">θ</div></div>' +
                '</div>')

        elif scene_type == "dismiss":
            anim_parts.append(f'<div class="tag" id="t{i}">🌬️ Small Effects</div>')
            anim_parts.append(f'<div class="hero" id="h{i}" style="font-size:54px">Air resistance and spin?</div>')
            anim_parts.append(f'<div class="dismiss-row" id="dr{i}">' +
                '<span class="dismiss-pill" id="d0">Air Resistance</span>' +
                '<span class="dismiss-pill" id="d1">Ball Spin</span>' +
                '</div>')
            anim_parts.append(f'<div class="sub" id="s{i}">We can ignore them for now. Small effects.</div>')

        elif scene_type == "activity":
            anim_parts.append(f'<div class="tag" id="t{i}">🔬 Activity 1.1</div>')
            anim_parts.append(f'<div class="hero" id="h{i}" style="font-size:56px">Model a bicycle ride<br>from school to home</div>')
            anim_parts.append(f'<div class="sub" id="s{i}">What details would YOU keep?</div>')

        elif scene_type == "finale":
            anim_parts.append(f'<div class="final-num" id="fn{i}"><span class="g">3</span></div>')
            anim_parts.append(f'<div class="final-label" id="fl{i}">Steps of the Scientific Method</div>')
            anim_parts.append(f'<div class="final-meta" id="fm{i}">Build · Predict · Refine</div>')

        anim_stack = "\n          ".join(anim_parts)

        scene = f'''
        <section class="clip" id="s{i}" data-start="{start}" data-duration="{dur}">
          <div class="scene-content" id="sc-{i}">
            {anim_stack}
          </div>
        </section>'''
        scenes_html.append(scene)

        # ── GSAP timeline ──
        if scene_type == "hook":
            timeline.append(f'      tl.from("#t{i}",{{y:-16,opacity:0,duration:0.3,ease:"power2.out"}},{d0});')
            timeline.append(f'      tl.from("#h{i}",{{y:60,scale:0.85,opacity:0,duration:0.7,ease:"elastic.out(1,0.45)"}},{round(d0+0.15,3)});')
            timeline.append(f'      tl.from("#s{i}",{{y:20,opacity:0,duration:0.4,ease:"power2.out"}},{round(d0+0.75,3)});')

        elif scene_type == "pill":
            timeline.append(f'      tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            if i == 1:
                for ki in range(4):
                    timeline.append(f'      tl.from("#p1-{ki}",{{scale:0,opacity:0,rotation:-20,duration:0.45,ease:"back.out(3)"}},{round(d0+0.2+ki*0.08,3)});')
                timeline.append(f'      tl.from("#s{i}",{{y:20,opacity:0,duration:0.35}},{round(d0+0.6,3)});')
            else:
                for ki in range(3):
                    timeline.append(f'      tl.from("#p2-{ki}",{{scale:0,opacity:0,rotation:-15,duration:0.45,ease:"back.out(3)"}},{round(d0+0.2+ki*0.08,3)});')
                for ki in range(2):
                    timeline.append(f'      tl.from("#p2-{ki+3}",{{scale:0,opacity:0,rotation:-15,duration:0.45,ease:"back.out(3)"}},{round(d0+0.5+ki*0.08,3)});')

        elif scene_type == "think":
            timeline.append(f'      tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            timeline.append(f'      tl.from("#q{i}",{{y:24,opacity:0,duration:0.35,ease:"power2.out"}},{round(d0+0.15,3)});')
            timeline.append(f'      tl.from("#a{i}",{{x:-60,opacity:0,duration:0.55,ease:"power3.out"}},{round(d0+0.45,3)});')

        elif scene_type == "stat":
            timeline.append(f'      tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            for ki in range(3):
                pos = [("x",-80),("y",60),("x",80)][ki]
                timeline.append(f'      tl.from("#sc{ki}",{{{pos[0]}:{pos[1]},opacity:0,duration:0.5,ease:"power3.out"}},{round(d0+0.2+ki*0.18,3)});')

        elif scene_type == "dismiss":
            timeline.append(f'      tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            timeline.append(f'      tl.from("#h{i}",{{y:30,opacity:0,duration:0.4,ease:"power2.out"}},{round(d0+0.15,3)});')
            timeline.append(f'      tl.from("#d0",{{scale:1,opacity:1}},{round(d0+0.5,3)});')
            timeline.append(f'      tl.to("#d0",{{scale:0.3,opacity:0,duration:0.5,ease:"power3.in"}},{round(d0+1.5,3)});')
            timeline.append(f'      tl.from("#d1",{{scale:1,opacity:1}},{round(d0+0.7,3)});')
            timeline.append(f'      tl.to("#d1",{{scale:0.3,opacity:0,duration:0.5,ease:"power3.in"}},{round(d0+1.7,3)});')
            timeline.append(f'      tl.from("#s{i}",{{y:20,opacity:0,duration:0.35}},{round(d0+2.0,3)});')

        elif scene_type == "activity":
            timeline.append(f'      tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            timeline.append(f'      tl.from("#h{i}",{{y:40,scale:0.9,opacity:0,duration:0.6,ease:"power3.out"}},{round(d0+0.15,3)});')
            timeline.append(f'      tl.from("#s{i}",{{y:20,opacity:0,duration:0.35}},{round(d0+0.65,3)});')

        elif scene_type == "finale":
            timeline.append(f'      tl.from("#fn{i}",{{scale:4,opacity:0,rotation:15,duration:0.7,ease:"elastic.out(1,0.35)"}},{d0});')
            timeline.append(f'      tl.from("#fl{i}",{{y:24,opacity:0,duration:0.4,ease:"power2.out"}},{round(d0+0.6,3)});')
            timeline.append(f'      tl.from("#fm{i}",{{y:12,opacity:0,duration:0.35,ease:"power2.out"}},{round(d0+0.95,3)});')

        # Exit
        if i + 1 < n:
            nx = round(segments[i+1]["start"], 3)
            timeline.append(f'      tl.to("#sc-{i}",{{opacity:0,y:-15,duration:0.25,ease:"power2.in"}},{round(nx-0.25,3)});')
            timeline.append(f'      tl.set("#s{i}",{{opacity:0}},{nx});')

    scenes_str = "\n".join(scenes_html)
    tl_str = "\n".join(timeline)

    html = f'''<!doctype html>
<html lang="en">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=1080,height=1920"/>
<script src="vendor/gsap.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:1080px;height:1920px;overflow:hidden;background:#0E1420;font-family:'Inter',sans-serif;color:#E8E9F0}}
#root{{position:relative;width:1080px;height:1920px;overflow:hidden;z-index:0}}

/* ── Background: glow + graph paper grid + grain ── */
.bg{{position:absolute;inset:-200px;
  background:radial-gradient(ellipse at 30% 20%,rgba(77,143,247,0.07),transparent 60%),
             radial-gradient(ellipse at 70% 80%,rgba(139,127,232,0.05),transparent 55%);
  pointer-events:none;z-index:0}}
.bg-grid{{position:absolute;inset:0;pointer-events:none;z-index:0;opacity:0.8}}
.bg-grain{{position:absolute;inset:0;pointer-events:none;z-index:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
  opacity:0.4}}

/* ── Top bar ── */
.topbar{{position:absolute;top:0;left:0;right:0;height:100px;padding:24px 50px;display:flex;justify-content:space-between;align-items:flex-start;z-index:10}}
.badge{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:500;letter-spacing:3px;text-transform:uppercase;color:#4D8FF7}}
.chap{{font-family:'Inter',sans-serif;font-size:22px;font-weight:400;color:#9A9BA3;margin-top:4px;letter-spacing:-0.3px}}
.cls-tag{{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:500;letter-spacing:2px;color:#6B7280}}
.part-tag{{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:400;letter-spacing:2px;color:#6B7280;opacity:0.6;text-align:right}}

/* ── Clips ── */
.clip{{position:absolute;inset:0;z-index:2}}
.scene-content{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:120px 60px 100px;z-index:3}}

/* ── Shared ── */
.tag{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:500;letter-spacing:4px;text-transform:uppercase;color:#7AC4F5;margin-bottom:20px}}
.hero{{font-family:'Inter',sans-serif;font-weight:900;font-size:100px;line-height:1.1;text-align:center;max-width:1000px;filter:drop-shadow(0 0 60px rgba(77,143,247,0.25));color:#FFFFFF;margin-bottom:20px}}
.hero .g{{background:linear-gradient(135deg,#4D8FF7,#7AC4F5,#8B7FE8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.sub{{font-family:'Inter',sans-serif;font-weight:400;font-size:28px;color:#9A9BA3;text-align:center;max-width:800px;letter-spacing:-0.3px}}

/* ── Content backdrop card ── */
.content-bd{{position:absolute;left:30px;right:30px;top:100px;bottom:30px;z-index:1;
  border-radius:36px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.05);
  backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);box-shadow:0 0 80px rgba(77,143,247,0.03) inset}}

/* ── Pills ── */
.pill-row{{text-align:center}}
.pill{{display:inline-block;padding:22px 48px;margin:10px;border-radius:999px;background:rgba(77,143,247,0.18);border:2px solid rgba(77,143,247,0.3);font-size:54px;font-weight:700;letter-spacing:-0.5px;color:#FFFFFF;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);box-shadow:0 8px 32px rgba(0,0,0,0.4)}}
.pill.green{{background:rgba(52,211,153,0.15);border-color:rgba(52,211,153,0.3);color:#6EE7B7}}
.pill.dim{{background:rgba(255,255,255,0.04);border-color:rgba(255,255,255,0.1);color:#9A9BA3}}

/* ── Think It Over ── */
.think-q{{font-size:40px;text-align:center;color:#E8E9F0;max-width:880px;margin-bottom:20px;font-weight:600;line-height:1.35}}
.think-a{{background:rgba(77,143,247,0.06);border:2px solid rgba(77,143,247,0.15);border-radius:24px;padding:28px 32px;max-width:880px;position:relative;overflow:hidden;backdrop-filter:blur(12px)}}
.think-a::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:linear-gradient(180deg,#4D8FF7,#7AC4F5)}}
.tal{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:600;color:#4D8FF7;letter-spacing:2px;margin-bottom:10px}}
.tatx{{font-size:32px;line-height:1.4;color:#E8E9F0;font-weight:400}}

/* ── Stat cards ── */
.stat-row{{display:flex;gap:14px;justify-content:center}}
.stat-card{{width:250px;padding:28px 16px 24px;border-radius:28px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);text-align:center;position:relative;overflow:hidden;backdrop-filter:blur(12px);box-shadow:0 8px 32px rgba(0,0,0,0.3)}}
.stat-card::before{{content:'';position:absolute;top:0;left:0;width:100%;height:4px;background:linear-gradient(90deg,#4D8FF7,#7AC4F5);transform:scaleX(0);transform-origin:left}}
.stat-num{{font-size:56px;font-weight:800;background:linear-gradient(135deg,#4D8FF7,#7AC4F5);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:6px}}
.stat-label{{font-size:22px;color:#E8E9F0;font-weight:500}}
.stat-unit{{font-family:'JetBrains Mono',monospace;font-size:15px;color:#6B7280;margin-top:8px;letter-spacing:2px}}

/* ── Dismiss ── */
.dismiss-row{{text-align:center;margin:16px 0}}
.dismiss-pill{{display:inline-block;padding:20px 44px;margin:10px;border-radius:999px;background:rgba(255,255,255,0.06);border:2px solid rgba(255,255,255,0.1);font-size:42px;font-weight:600;color:#E8E9F0;backdrop-filter:blur(8px);box-shadow:0 4px 20px rgba(0,0,0,0.3)}}

/* ── Finale ── */
.final-num{{font-size:180px;font-weight:900;letter-spacing:-8px;text-align:center;line-height:1;filter:drop-shadow(0 0 100px rgba(77,143,247,0.3))}}
.final-num .g{{background:linear-gradient(135deg,#4D8FF7,#7AC4F5,#8B7FE8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.final-label{{font-size:30px;color:#E8E9F0;text-align:center;margin-top:8px;font-weight:500;letter-spacing:-0.3px}}
.final-meta{{font-family:'JetBrains Mono',monospace;font-size:20px;color:#6B7280;text-align:center;margin-top:16px;letter-spacing:3px}}

/* ── Progress ── */
.progress{{position:absolute;bottom:0;left:0;height:3px;width:0%;background:linear-gradient(90deg,#4D8FF7,#7AC4F5);z-index:10}}

audio{{display:none}}
</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="1080" data-height="1920">
  <div class="bg"></div>
  <svg class="bg-grid">{grid_lines}</svg>
  <div class="bg-grain"></div>
  <div class="content-bd" id="main-bd"></div>

  <div class="topbar">
    <div><div class="badge">{esc(subject)}</div><div class="chap">{esc(title)}</div></div>
    <div><div class="cls-tag">CLASS {esc(cls)}</div><div class="part-tag">{esc(part)}</div></div>
  </div>

  {scenes_str}

  <div class="progress" id="progress"></div>
</div>

<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{paused:true}});

{tl_str}

tl.fromTo("#progress",{{width:"0%"}},{{width:"100%",ease:"none",duration:{total}}},0);
window.__timelines["main"] = tl;
</script>
</body>
</html>'''
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    args = ap.parse_args()

    out_dir = os.path.join(ROOT, "pipeline", "rendered", args.slug)
    os.makedirs(os.path.join(out_dir, "vendor"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "pipeline", "vendor", "gsap.min.js"),
                os.path.join(out_dir, "vendor", "gsap.min.js"))

    script = json.load(open(os.path.join(out_dir, "script.json")))
    segs = script["segments"]
    title = script.get("title", "Untitled")
    subject = script.get("subject", "")
    cls = script.get("class", "")
    part = script.get("part", "")

    # ── TTS per segment ──
    seg_dir = os.path.join(out_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    total_audio = []
    for i, s in enumerate(segs):
        text = s["text"].strip()
        wav = os.path.join(seg_dir, f"seg_{i:02d}.wav")
        if not os.path.exists(wav) or os.path.getsize(wav) < 2000:
            print(f"[TTS] seg {i}...", end=" ", flush=True)
            tts_edge(text, wav, args.voice)
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

    # ── Assign timing ──
    running = 0.0
    for s in segs:
        s["start"] = round(running, 3)
        s["end"] = round(running + s["duration"], 3)
        running += s["duration"]

    # ── Build HTML ──
    html = build_html(segs, title, subject, cls, part)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # ── Meta ──
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump({"total": round(total_dur,3), "segments": len(segs)}, f)
    with open(os.path.join(out_dir, "hyperframes.json"), "w") as f:
        json.dump({"composition":"main","audio":"narration.wav"}, f)

    print(f"[done] {out_dir}/index.html ({total_dur:.2f}s, {len(segs)} segments)")
    print(json.dumps({"slug":args.slug,"duration":round(total_dur,3),"segments":len(segs)}))

if __name__ == "__main__":
    sys.exit(main())