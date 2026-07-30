"""Generate final HTML with images, no karaoke dependencies."""
import json, os
from pathlib import Path
import shutil

SLUG = "c9-exploration-ch1"
BASE = Path.home() / "projects" / "cbse-youtube-channel" / "pipeline" / "rendered" / SLUG

with open(BASE / "manifest.json") as f:
    manifest = json.load(f)
with open(BASE / "script.json") as f:
    script_data = json.load(f)

segments = manifest.get("segments", [])
total_dur = segments[-1]["start"] + segments[-1].get("duration", 5) + 0.5 if segments else 0

# Build CSS vars for each segment
labels_data = []
for i, seg in enumerate(segments):
    text = seg.get("text", "")
    tag = ""
    for m in ["[ACTIVITY]", "[THINK IT OVER]", "[DID YOU KNOW]", "[HINT]", "[READY TO GO BEYOND]"]:
        if m in text:
            text = text.replace(m, "").strip()
            tag = m[1:-1].replace("_", " ").title()
    
    start = seg.get("start", 0)
    dur = seg.get("duration", 5)
    nxt = segments[i+1]["start"] if i+1 < len(segments) else start + dur + 0.5
    clip_dur = nxt - start
    img_path = "images/seg_{:02d}.png".format(i)
    img_exists = (BASE / img_path).exists()
    
    labels_data.append({
        "i": i, "start": start, "dur": clip_dur,
        "text": text, "tag": tag,
        "img": img_path if img_exists else "",
        "has_img": img_exists
    })

# Generate HTML
lines = ['<!doctype html><html lang="en"><head>']
lines.append('<meta charset="UTF-8"/>')
lines.append('<meta name="viewport" content="width=device-width,initial-scale=1.0"/>')
lines.append('<title>What Is a Scientific Model?</title>')
lines.append('<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>')
lines.append("""<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;overflow:hidden;background:#0E1420;font-family:'Inter',sans-serif;color:#E8E9F0;-webkit-font-smoothing:antialiased}
#bg{position:absolute;inset:-200px;background:radial-gradient(circle at 30% 25%,rgba(77,143,247,0.08),transparent 60%),radial-gradient(circle at 75% 80%,rgba(139,127,232,0.06),transparent 55%);pointer-events:none;z-index:0}
.clip{position:absolute;inset:0;z-index:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:120px 60px;opacity:0}
.label{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:600;letter-spacing:3px;text-transform:uppercase;color:#4D8FF7;margin-bottom:16px;align-self:flex-start}
.tag{font-family:'JetBrains Mono',monospace;font-size:18px;color:#7AC4F5;letter-spacing:2px;margin-top:-12px;margin-bottom:20px;align-self:flex-start}
.imgwrap{width:720px;border-radius:20px;overflow:hidden;box-shadow:0 12px 48px rgba(0,0,0,0.5);position:relative;margin-bottom:40px}
.imgwrap img{width:100%;display:block}
.imgframe{position:absolute;inset:0;border:2px solid rgba(255,255,255,0.06);border-radius:20px;pointer-events:none}
.imgglow{position:absolute;inset:-2px;border-radius:22px;background:linear-gradient(135deg,transparent 30%,rgba(77,143,247,0.12) 100%);pointer-events:none}
.cap{font-size:36px;line-height:1.35;text-align:center;max-width:880px;color:#E8E9F0}
#progress{position:absolute;left:0;bottom:0;height:5px;width:0%;background:linear-gradient(90deg,#4D8FF7,#7AC4F5);z-index:12}
#grain{position:absolute;inset:0;z-index:10;pointer-events:none;opacity:.03}
#grain svg{width:100%;height:100%}
audio{display:none}
</style></head><body>""")

lines.append('<div id="root" data-composition-id="main" data-start="0" data-width="1080" data-height="1920" data-duration="{:.1f}" data-fps="30">'.format(total_dur))
lines.append('<div id="bg"></div>')
lines.append('<div id="grain"><svg viewBox="0 0 1080 1920"><filter id="n"><feTurbulence type="fractalNoise" baseFrequency="0.65" numOctaves="3" stitchTiles="stitch"/></filter><rect width="1080" height="1920" filter="url(#n)"/></svg></div>')

for ld in labels_data:
    bg = '' if ld["has_img"] else 'style="background:linear-gradient(135deg,#181F33,#212944);min-height:540px"'
    img_tag = '<img src="{src}" alt="" {bg}/>'.format(src=ld["img"], bg=bg) if ld["img"] else '<div style="width:720px;height:540px;background:linear-gradient(135deg,#181F33,#212944)"></div>'
    tag_html = '<div class="tag">{t}</div>'.format(t=ld["tag"]) if ld["tag"] else ''
    
    lines.append("""
<div class="clip" id="p{id}" data-track-index="{tr}" data-start="{st}" data-duration="{dur}">
  <div class="label">{label}</div>
  {tag}
  <div class="imgwrap">{img}<div class="imgframe"></div><div class="imgglow"></div></div>
  <div class="cap">{text}</div>
</div>""".format(
        id=ld["i"], tr=ld["i"]+1, st=round(ld["start"],2), dur=round(ld["dur"],2),
        label="Science \u00b7 Class 9" + (" \u00b7 " + ld["tag"] if ld["tag"] else ""),
        tag=tag_html, img=img_tag, text=ld["text"]
    ))

lines.append('<audio src="narration.wav" data-start="0" data-duration="{:.1f}" data-volume="1" preload="auto"></audio>'.format(total_dur))
lines.append('<div id="progress"></div></div>')

# GSAP timeline
lines.append('<script>(function(){var tl=gsap.timeline({paused:true});')
for ld in labels_data:
    st = ld["start"]
    end = st + ld["dur"]
    lines.append('tl.set("#p{id}",{{opacity:1}},{st});'.format(id=ld["i"], st=round(st,2)))
    lines.append('tl.from("#p{id} .imgwrap",{{opacity:0,y:30,scale:.95,duration:.5,ease:"power2.out"}},{st2});'.format(id=ld["i"], st2=round(st+0.15,2)))
    lines.append('tl.from("#p{id} .cap",{{opacity:0,y:16,duration:.35,ease:"power2.out"}},{st3});'.format(id=ld["i"], st3=round(st+0.45,2)))
    lines.append('tl.from("#p{id} .label",{{opacity:0,y:-8,duration:.25}},{st});'.format(id=ld["i"], st=round(st,2)))
    lines.append('tl.to("#p{id}",{{opacity:0,duration:.25}},{end});'.format(id=ld["i"], end=round(end-0.25,2)))

lines.append('window.__timelines=window.__timelines||{};window.__timelines["main"]=tl;})();</script></body></html>')

with open(BASE / "index.html", "w") as f:
    f.write('\n'.join(lines))

print("Done. Lines:", len(lines))
print("Duration:", round(total_dur, 1))
print("Images available:", sum(1 for ld in labels_data if ld["has_img"]), "/", len(labels_data))