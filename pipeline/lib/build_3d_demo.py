#!/usr/bin/env python3
"""build_3d_demo.py — 3D cricket ball demo with GSAP 3D transforms, existing images, edge-tts."""

import argparse, json, os, re, subprocess, sys, shutil

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

    # Images from the existing set
    img_dir = "images"

    for i, s in enumerate(segments):
        start = round(s["start"], 3)
        dur = round(s["duration"], 3)
        d0 = start + 0.05

        img_path = f"{img_dir}/seg_{i:02d}.png"

        scene_type = ["hook", "mass", "speed", "direction", "trajectory", "finale"][i]

        anim_parts = []

        if scene_type == "hook":
            # 3D ball floating + spinning + question text
            anim_parts.append(f'''
            <div class="ball-container" id="bc{i}">
              <div class="ball-3d" id="ball{i}">
                <div class="ball-sphere" id="bsphere{i}">
                  <svg class="ball-seams" viewBox="0 0 200 200">
                    <path d="M30,40 Q100,-10 170,40" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M30,160 Q100,210 170,160" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M40,30 Q-10,100 40,170" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M160,30 Q210,100 160,170" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <ellipse cx="100" cy="100" rx="40" ry="90" fill="none" stroke="#E8E9F0" stroke-width="2" opacity="0.4"/>
                  </svg>
                  <div class="ball-shine"></div>
                </div>
              </div>
            </div>
            <div class="tag" id="t{i}">Science · Class 9</div>
            <div class="hero" id="h{i}">How do we understand<br>a <span class="g">cricket ball</span> in motion?</div>
            <div class="sub" id="s{i}">Let's build a model in 3D.</div>''')

        elif scene_type == "mass":
            anim_parts.append(f'''
            <div class="ball-container sm" id="bc{i}">
              <div class="ball-3d" id="ball{i}">
                <div class="ball-sphere" id="bsphere{i}">
                  <svg class="ball-seams" viewBox="0 0 200 200">
                    <path d="M30,40 Q100,-10 170,40" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M30,160 Q100,210 170,160" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <ellipse cx="100" cy="100" rx="40" ry="90" fill="none" stroke="#E8E9F0" stroke-width="2" opacity="0.4"/>
                  </svg>
                  <div class="ball-shine"></div>
                </div>
              </div>
              <div class="force-arrow mass-arrow" id="ma{i}">
                <svg viewBox="0 0 100 40" width="240" height="96">
                  <line x1="10" y1="20" x2="80" y2="20" stroke="#4D8FF7" stroke-width="6" stroke-linecap="round"/>
                  <polygon points="80,8 100,20 80,32" fill="#4D8FF7"/>
                </svg>
              </div>
            </div>
            <div class="tag" id="t{i}">📊 Key Quantity #1</div>
            <div class="stat-big" id="st{i}"><span class="g">Mass</span></div>
            <div class="sub" id="s{i}">How much force to change its motion</div>
            <div class="pill-row" id="pr{i}">
              <span class="pill" id="p{i}">m  ·  kg</span>
            </div>''')

        elif scene_type == "speed":
            anim_parts.append(f'''
            <div class="ball-container sm" id="bc{i}">
              <div class="ball-3d" id="ball{i}">
                <div class="ball-sphere speed-glow" id="bsphere{i}">
                  <svg class="ball-seams" viewBox="0 0 200 200">
                    <path d="M30,40 Q100,-10 170,40" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M30,160 Q100,210 170,160" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <ellipse cx="100" cy="100" rx="40" ry="90" fill="none" stroke="#E8E9F0" stroke-width="2" opacity="0.4"/>
                  </svg>
                  <div class="ball-shine"></div>
                </div>
              </div>
              <div class="speed-trails" id="stl{i}">
                <svg viewBox="0 0 200 40" width="400" height="80">
                  <line x1="10" y1="20" x2="90" y2="20" stroke="#7AC4F5" stroke-width="4" stroke-linecap="round" opacity="0.3"/>
                  <line x1="10" y1="20" x2="70" y2="20" stroke="#7AC4F5" stroke-width="3" stroke-linecap="round" opacity="0.5"/>
                  <line x1="10" y1="20" x2="50" y2="20" stroke="#7AC4F5" stroke-width="2" stroke-linecap="round" opacity="0.7"/>
                </svg>
              </div>
            </div>
            <div class="tag" id="t{i}">📊 Key Quantity #2</div>
            <div class="stat-big" id="st{i}"><span class="g2">Speed</span></div>
            <div class="sub" id="s{i}">How fast is it traveling through air?</div>
            <div class="pill-row" id="pr{i}">
              <span class="pill" id="p{i}">v  ·  m/s</span>
            </div>''')

        elif scene_type == "direction":
            anim_parts.append(f'''
            <div class="angle-container" id="ac{i}">
              <div class="angle-arc" id="aa{i}">
                <svg viewBox="0 0 300 300" width="360" height="360">
                  <line x1="150" y1="280" x2="150" y2="40" stroke="#6B7280" stroke-width="2" stroke-dasharray="6 4"/>
                  <line x1="150" y1="280" x2="250" y2="80" stroke="#4D8FF7" stroke-width="6" stroke-linecap="round" id="angle-line{i}"/>
                  <path d="M150 220 A80 80 0 0 1 210 120" fill="none" stroke="#7AC4F5" stroke-width="3" id="angle-arc-path{i}"/>
                  <text x="195" y="185" fill="#7AC4F5" font-size="28" font-family="serif" font-style="italic">θ</text>
                </svg>
              </div>
              <div class="ball-container vsm" id="bc{i}b" style="position:absolute;right:60px;top:80px">
                <div class="ball-3d" id="ball{i}b">
                  <div class="ball-sphere" id="bsphere{i}b">
                    <div class="ball-shine"></div>
                  </div>
                </div>
              </div>
            </div>
            <div class="tag" id="t{i}">📊 Key Quantity #3</div>
            <div class="stat-big" id="st{i}"><span class="g3">Direction</span></div>
            <div class="sub" id="s{i}">The angle of launch decides the path</div>''')

        elif scene_type == "trajectory":
            anim_parts.append(f'''
            <div class="traj-container" id="tc{i}">
              <svg viewBox="0 0 900 500" width="900" height="500">
                <path d="M50,450 Q200,50 500,100 Q700,150 850,430" fill="none" stroke="#4D8FF7" stroke-width="4" stroke-dasharray="800" stroke-dashoffset="800" id="traj-path{i}"/>
                <circle cx="50" cy="450" r="12" fill="#4D8FF7" opacity="0.8" id="traj-ball{i}"/>
                <circle cx="50" cy="450" r="6" fill="#E8E9F0" id="traj-core{i}"/>
                <line x1="50" y1="450" x2="200" y2="350" stroke="#7AC4F5" stroke-width="3" stroke-dasharray="4" id="traj-v{i}"/>
                <text x="100" y="420" fill="#7AC4F5" font-size="24" font-family="mono">v</text>
                <line x1="850" y1="430" x2="880" y2="430" stroke="#6B7280" stroke-width="2" stroke-dasharray="4"/>
                <text x="820" y="420" fill="#6B7280" font-size="18" font-family="mono">Boundary</text>
              </svg>
            </div>
            <div class="tag" id="t{i}">🔮 The Prediction</div>
            <div class="hero" id="h{i}" style="font-size:52px">Mass + Speed + Direction<br>= <span class="g">Trajectory</span></div>''')

        elif scene_type == "finale":
            anim_parts.append(f'''
            <div class="ball-container" id="bc{i}">
              <div class="ball-3d" id="ball{i}">
                <div class="ball-sphere" id="bsphere{i}">
                  <svg class="ball-seams" viewBox="0 0 200 200">
                    <path d="M30,40 Q100,-10 170,40" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M30,160 Q100,210 170,160" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M40,30 Q-10,100 40,170" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <path d="M160,30 Q210,100 160,170" fill="none" stroke="#E8E9F0" stroke-width="2.5" opacity="0.6"/>
                    <ellipse cx="100" cy="100" rx="40" ry="90" fill="none" stroke="#E8E9F0" stroke-width="2" opacity="0.4"/>
                  </svg>
                  <div class="ball-shine"></div>
                </div>
              </div>
              <div class="orbit-ring" id="or{i}">
                <svg viewBox="0 0 300 300" width="380" height="380">
                  <ellipse cx="150" cy="150" rx="140" ry="60" fill="none" stroke="rgba(77,143,247,0.2)" stroke-width="2" transform="rotate(-20 150 150)"/>
                </svg>
              </div>
            </div>
            <div class="final-num" id="fn{i}"><span class="g">3</span></div>
            <div class="final-label" id="fl{i}">Simple quantities. Powerful predictions.</div>
            <div class="final-meta" id="fm{i}">Mass · Speed · Direction</div>''')

        anim_stack = "".join(anim_parts)

        scene = f'''<section class="clip" id="s{i}" data-start="{start}" data-duration="{dur}">
          <div class="scene-content" id="sc-{i}">{anim_stack}</div>
        </section>'''
        scenes_html.append(scene)

        # ── GSAP 3D timeline ──
        if scene_type == "hook":
            timeline.append(f'tl.from("#t{i}",{{y:-16,opacity:0,duration:0.3}},{d0});')
            timeline.append(f'tl.from("#h{i}",{{y:60,scale:0.85,opacity:0,duration:0.7,ease:"elastic.out(1,0.45)"}},{round(d0+0.15,3)});')
            timeline.append(f'tl.from("#s{i}",{{y:20,opacity:0,duration:0.4}},{round(d0+0.75,3)});')
            # 3D ball: float in + spin continuously
            timeline.append(f'tl.from("#ball{i}",{{y:-120,opacity:0,duration:0.8,ease:"power3.out"}},{round(d0+0.3,3)});')
            timeline.append(f'tl.to("#ball{i}",{{rotationY:360,rotationX:15,duration:{dur-0.5},ease:"none"}},{d0+0.5});')

        elif scene_type == "mass":
            timeline.append(f'tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            timeline.append(f'tl.from("#st{i}",{{y:40,scale:0.8,opacity:0,duration:0.6,ease:"back.out(2)"}},{round(d0+0.15,3)});')
            timeline.append(f'tl.from("#bc{i}",{{x:200,opacity:0,duration:0.5,ease:"power3.out"}},{d0});')
            timeline.append(f'tl.to("#ball{i}",{{rotationY:360,duration:{dur-0.5},ease:"none"}},{d0});')
            timeline.append(f'tl.from("#ma{i}",{{scaleX:0,transformOrigin:"left center",opacity:0,duration:0.5,ease:"back.out(2)"}},{round(d0+0.4,3)});')
            timeline.append(f'tl.from("#pr{i}",{{y:20,opacity:0,duration:0.35}},{round(d0+0.7,3)});')

        elif scene_type == "speed":
            timeline.append(f'tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            timeline.append(f'tl.from("#st{i}",{{y:40,scale:0.8,opacity:0,duration:0.6,ease:"back.out(2)"}},{round(d0+0.15,3)});')
            timeline.append(f'tl.from("#bc{i}",{{x:-200,opacity:0,duration:0.5,ease:"power3.out"}},{d0});')
            timeline.append(f'tl.to("#ball{i}",{{rotationY:-360,duration:{dur-0.5},ease:"none"}},{d0});')
            timeline.append(f'tl.fromTo("#stl{i}",{{scaleX:0,transformOrigin:"left center"}},{{scaleX:1,duration:0.6,ease:"power3.out"}},{round(d0+0.4,3)});')
            timeline.append(f'tl.from("#pr{i}",{{y:20,opacity:0,duration:0.35}},{round(d0+0.7,3)});')

        elif scene_type == "direction":
            timeline.append(f'tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            timeline.append(f'tl.from("#st{i}",{{y:40,scale:0.8,opacity:0,duration:0.6,ease:"back.out(2)"}},{round(d0+0.15,3)});')
            timeline.append(f'tl.from("#ac{i}",{{scale:0.5,opacity:0,duration:0.6,ease:"back.out(2)"}},{d0});')
            timeline.append(f'tl.to("#angle-line{i}",{{transformOrigin:"150px 280px",rotation:-35,duration:{dur-0.5},ease:"power1.inOut"}},{d0+0.3});')
            timeline.append(f'tl.from("#ball{i}b",{{y:-60,opacity:0,duration:0.5,ease:"power3.out"}},{d0+0.2});')
            timeline.append(f'tl.to("#ball{i}b",{{rotationY:360,duration:{dur-0.5},ease:"none"}},{d0+0.2});')

        elif scene_type == "trajectory":
            timeline.append(f'tl.from("#t{i}",{{y:-16,opacity:0,duration:0.25}},{d0});')
            timeline.append(f'tl.from("#h{i}",{{y:30,opacity:0,duration:0.4}},{round(d0+0.15,3)});')
            timeline.append(f'tl.from("#tc{i}",{{y:40,opacity:0,duration:0.5,ease:"power2.out"}},{round(d0+0.3,3)});')
            # Draw trajectory path
            timeline.append(f'tl.to("#traj-path{i}",{{strokeDashoffset:0,duration:{min(4.0,dur-1.0)},ease:"power2.inOut"}},{round(d0+0.5,3)});')
            # Move ball along path
            timeline.append(f'tl.to("#traj-ball{i}",{{morphSVG:{{path:"#traj-path{i}"}},duration:{min(4.0,dur-1.0)},ease:"power1.inOut"}},{round(d0+0.5,3)});')
            timeline.append(f'tl.to("#traj-core{i}",{{morphSVG:{{path:"#traj-path{i}"}},duration:{min(4.0,dur-1.0)},ease:"power1.inOut"}},{round(d0+0.5,3)});')

        elif scene_type == "finale":
            timeline.append(f'tl.from("#ball{i}",{{scale:0,rotationY:720,opacity:0,duration:0.8,ease:"elastic.out(1,0.35)"}},{d0});')
            timeline.append(f'tl.to("#or{i}",{{rotation:360,duration:{dur-0.5},ease:"none"}},{d0});')
            timeline.append(f'tl.from("#fn{i}",{{scale:4,opacity:0,rotation:15,duration:0.7,ease:"elastic.out(1,0.35)"}},{round(d0+0.3,3)});')
            timeline.append(f'tl.from("#fl{i}",{{y:24,opacity:0,duration:0.4}},{round(d0+0.9,3)});')
            timeline.append(f'tl.from("#fm{i}",{{y:12,opacity:0,duration:0.35}},{round(d0+1.2,3)});')

        # Exit
        if i + 1 < n:
            nx = round(segments[i+1]["start"], 3)
            timeline.append(f'tl.to("#sc-{i}",{{opacity:0,y:-15,duration:0.25,ease:"power2.in"}},{round(nx-0.25,3)});')
            timeline.append(f'tl.set("#s{i}",{{opacity:0}},{nx});')

    scenes_str = "\n".join(scenes_html)
    tl_str = "\n".join(timeline)

    # Graph paper grid
    grid_lines = "".join(
        f'<line x1="{x}" y1="0" x2="{x}" y2="1920" stroke="rgba(77,143,247,0.03)" stroke-width="0.5"/>'
        for x in range(0, 1081, 30)
    ) + "".join(
        f'<line x1="0" y1="{y}" x2="1080" y2="{y}" stroke="rgba(77,143,247,0.03)" stroke-width="0.5"/>'
        for y in range(0, 1921, 30)
    )

    html = f'''<!doctype html>
<html lang="en">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=1080,height=1920"/>
<script src="vendor/gsap.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/MorphSVGPlugin.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:1080px;height:1920px;overflow:hidden;background:#0E1420;font-family:'Inter',sans-serif;color:#E8E9F0;perspective:1200px}}
#root{{position:relative;width:1080px;height:1920px;overflow:hidden;z-index:0;perspective:1200px}}

.bg{{position:absolute;inset:-200px;
  background:radial-gradient(ellipse at 30% 20%,rgba(77,143,247,0.08),transparent 60%),
             radial-gradient(ellipse at 70% 80%,rgba(139,127,232,0.06),transparent 55%);
  pointer-events:none;z-index:0}}
.bg-grid{{position:absolute;inset:0;pointer-events:none;z-index:0;opacity:0.6}}
.bg-grain{{position:absolute;inset:0;pointer-events:none;z-index:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");opacity:0.4}}

.topbar{{position:absolute;top:0;left:0;right:0;height:100px;padding:24px 50px;display:flex;justify-content:space-between;align-items:flex-start;z-index:10}}
.badge{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:500;letter-spacing:3px;text-transform:uppercase;color:#4D8FF7}}
.chap{{font-family:'Inter',sans-serif;font-size:22px;font-weight:400;color:#9A9BA3;margin-top:4px;letter-spacing:-0.3px}}
.cls-tag{{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:500;letter-spacing:2px;color:#6B7280}}
.part-tag{{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:400;letter-spacing:2px;color:#6B7280;opacity:0.6;text-align:right}}

.clip{{position:absolute;inset:0;z-index:2}}
.scene-content{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:120px 60px 100px;z-index:3}}

.tag{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:500;letter-spacing:4px;text-transform:uppercase;color:#7AC4F5;margin-bottom:16px}}
.hero{{font-family:'Inter',sans-serif;font-weight:900;font-size:72px;line-height:1.12;text-align:center;max-width:960px;filter:drop-shadow(0 0 60px rgba(77,143,247,0.2));color:#FFFFFF;margin-bottom:12px}}
.hero .g{{background:linear-gradient(135deg,#4D8FF7,#7AC4F5,#8B7FE8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.sub{{font-family:'Inter',sans-serif;font-weight:400;font-size:26px;color:#9A9BA3;text-align:center;max-width:800px;letter-spacing:-0.3px}}

/* ── 3D Cricket Ball ── */
.ball-container{{position:relative;width:280px;height:280px;margin-bottom:10px}}
.ball-container.sm{{width:200px;height:200px;margin-bottom:4px}}
.ball-container.vsm{{width:120px;height:120px}}
.ball-3d{{width:100%;height:100%;transform-style:preserve-3d}}
.ball-sphere{{position:absolute;inset:0;border-radius:50%;
  background:radial-gradient(circle at 35% 35%,#D44040 0%,#B22222 40%,#8B1A1A 70%,#5C1010 100%);
  box-shadow:0 0 60px rgba(178,34,34,0.3),inset 0 -20px 40px rgba(0,0,0,0.3)}}
.ball-sphere.speed-glow{{box-shadow:0 0 80px rgba(122,196,245,0.3),inset 0 -20px 40px rgba(0,0,0,0.3)}}
.ball-seams{{position:absolute;inset:-10px;width:calc(100% + 20px);height:calc(100% + 20px)}}
.ball-shine{{position:absolute;top:15%;left:20%;width:30%;height:25%;
  border-radius:50%;background:radial-gradient(ellipse,rgba(255,255,255,0.25),transparent 70%)}}

.force-arrow{{position:absolute;top:-30px;right:-100px}}
.mass-arrow svg line{{stroke:#4D8FF7}}

.speed-trails{{position:absolute;bottom:-20px;left:50%;transform:translateX(-50%)}}

/* ── Angle ── */
.angle-container{{position:relative;width:400px;height:300px;margin-bottom:4px}}

/* ── Trajectory ── */
.traj-container{{width:900px;height:500px;margin-bottom:4px}}

/* ── Stat big ── */
.stat-big{{font-size:72px;font-weight:900;margin-bottom:8px}}
.stat-big .g{{background:linear-gradient(135deg,#4D8FF7,#7AC4F5);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.stat-big .g2{{background:linear-gradient(135deg,#7AC4F5,#8B7FE8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.stat-big .g3{{background:linear-gradient(135deg,#8B7FE8,#4D8FF7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}

.pill-row{{text-align:center;margin-top:6px}}
.pill{{display:inline-block;padding:12px 32px;border-radius:999px;background:rgba(77,143,247,0.15);border:2px solid rgba(77,143,247,0.25);font-size:36px;font-weight:600;color:#FFFFFF;backdrop-filter:blur(12px);font-family:'JetBrains Mono',monospace}}

.orbit-ring{{position:absolute;top:-40px;left:-40px;pointer-events:none}}

/* ── Finale ── */
.final-num{{font-size:160px;font-weight:900;letter-spacing:-6px;text-align:center;line-height:1;filter:drop-shadow(0 0 100px rgba(77,143,247,0.3))}}
.final-num .g{{background:linear-gradient(135deg,#4D8FF7,#7AC4F5,#8B7FE8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.final-label{{font-size:28px;color:#E8E9F0;text-align:center;margin-top:6px;font-weight:500}}
.final-meta{{font-family:'JetBrains Mono',monospace;font-size:20px;color:#6B7280;text-align:center;margin-top:12px;letter-spacing:3px}}

.progress{{position:absolute;bottom:0;left:0;height:3px;width:0%;background:linear-gradient(90deg,#4D8FF7,#7AC4F5);z-index:10}}
audio{{display:none}}
</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="1080" data-height="1920">
  <div class="bg"></div>
  <svg class="bg-grid">{grid_lines}</svg>
  <div class="bg-grain"></div>
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

    # Copy existing images from c9-exploration-ch1
    src_img_dir = os.path.join(ROOT, "pipeline", "rendered", "c9-exploration-ch1", "images")
    dst_img_dir = os.path.join(out_dir, "images")
    os.makedirs(dst_img_dir, exist_ok=True)
    for i in range(6):
        src = os.path.join(src_img_dir, f"seg_{i:02d}.png")
        dst = os.path.join(dst_img_dir, f"seg_{i:02d}.png")
        if os.path.exists(src):
            shutil.copy(src, dst)
            print(f"[img] copied seg_{i:02d}.png", file=sys.stderr)

    # TTS
    seg_dir = os.path.join(out_dir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    total_audio = []
    for i, s in enumerate(segs):
        text = s["text"].strip()
        wav = os.path.join(seg_dir, f"seg_{i:02d}.wav")
        if not os.path.exists(wav) or os.path.getsize(wav) < 2000:
            print(f"[TTS] seg {i}...", end=" ", flush=True)
            tts_edge(text, wav, args.voice)
            print(f"{os.path.getsize(wav)} bytes", file=sys.stderr)
        dur = probe_duration(wav)
        total_audio.append((i, wav, dur))
        s["duration"] = round(dur, 3)

    n_wav = os.path.join(out_dir, "narration.wav")
    concat_txt = os.path.join(out_dir, "_concat.txt")
    with open(concat_txt, "w") as f:
        for _, wav, _ in total_audio:
            f.write(f"file '{os.path.abspath(wav)}'\n")
    subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",concat_txt,"-c","copy",n_wav], capture_output=True, check=True)
    total_dur = probe_duration(n_wav)
    print(f"[audio] Total: {total_dur:.2f}s ({len(total_audio)} segments)")

    running = 0.0
    for s in segs:
        s["start"] = round(running, 3)
        s["end"] = round(running + s["duration"], 3)
        running += s["duration"]

    html = build_html(segs, title, subject, cls, part)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump({"total": round(total_dur,3), "segments": len(segs)}, f)
    with open(os.path.join(out_dir, "hyperframes.json"), "w") as f:
        json.dump({"composition":"main","audio":"narration.wav"}, f)

    print(f"[done] {out_dir}/index.html ({total_dur:.2f}s, {len(segs)} segments)")
    print(json.dumps({"slug":args.slug,"duration":round(total_dur,3),"segments":len(segs)}))

if __name__ == "__main__":
    sys.exit(main())