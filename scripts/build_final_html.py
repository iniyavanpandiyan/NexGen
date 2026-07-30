"""Generate final faceless explainer HTML."""
import json, os
from pathlib import Path

SLUG = "c9-exploration-ch1"
BASE = Path.home() / "projects" / "cbse-youtube-channel" / "pipeline" / "rendered" / SLUG
TEMPLATE = BASE / "faceless-template.html"

# Copy template into slug dir if not there
src = Path.home() / "projects" / "cbse-youtube-channel" / "pipeline" / "templates" / "faceless-template.html"
if not TEMPLATE.exists():
    import shutil
    shutil.copy(src, TEMPLATE)

with open(BASE / "manifest.json") as f:
    manifest = json.load(f)
with open(BASE / "script.json") as f:
    script_data = json.load(f)

segments = manifest.get("segments", [])
word_data = manifest.get("word_alignment", [])
prompts = [seg.get("image_prompt", "") for seg in script_data.get("segments", [])]

seg_words = {}
for w in word_data:
    si = w.get("segment", 0)
    seg_words.setdefault(si, []).append(w)

total_dur = segments[-1]["start"] + segments[-1].get("duration", 5) + 0.5 if segments else 0
phrases = []
phases_html = ""
tl_js = ""
track = 0

for i, seg in enumerate(segments):
    si = seg.get("index", i)
    start = seg.get("start", 0)
    dur = seg.get("duration", 5)
    text = seg.get("text", "")
    words = seg_words.get(si, [])
    nxt = segments[i+1]["start"] if i+1 < len(segments) else start + dur + 0.5
    clip_dur = nxt - start
    
    display = text
    tag = ""
    for m in ["[ACTIVITY]", "[THINK IT OVER]", "[DID YOU KNOW]", "[HINT]", "[READY TO GO BEYOND]"]:
        if m in display:
            display = display.replace(m, "").strip()
            tag = m[1:-1].replace("_", " ").title()
    
    img_path = "images/seg_{:02d}.png".format(si)
    img_exists = (BASE / img_path).exists()
    
    word_spans = ""
    word_arr = []
    for j, w in enumerate(words):
        wt = w.get("w", w.get("word", ""))
        ws = w.get("s", w.get("start", 0))
        we = w.get("e", w.get("end", 0))
        word_spans += '<span class="kw" data-p="{i}" data-w="{j}">{wt}</span> '.format(i=i, j=j, wt=wt)
        word_arr.append({"s": ws, "e": we})
    
    if not words:
        text_words = display.split()
        dur_per = clip_dur / max(len(text_words), 1)
        for j, wt in enumerate(text_words):
            ws = start + j * dur_per
            we = start + (j + 1) * dur_per
            word_spans += '<span class="kw" data-p="{i}" data-w="{j}">{wt}</span> '.format(i=i, j=j, wt=wt)
            word_arr.append({"s": round(ws,3), "e": round(we,3)})
    
    track += 1
    label_text = "Science \u00b7 Class 9" + (" \u00b7 " + tag if tag else "")
    bg_style = "background:linear-gradient(135deg,#181F33,#212944)" if not img_exists else ""
    
    phases_html += """
<div class="clip" id="p{id}" data-track-index="{tr}" data-start="{st}" data-duration="{dur}">
  <div class="phase">
    <div class="inner">
      <div class="label">{label}</div>
      <div class="img-wrap" style="position:relative">
        <img src="{img}" alt="" style="{bg}" />
        <div class="img-frame"></div>
        <div class="img-glow"></div>
      </div>
      <div class="cap" id="cap_{id}">{spans}</div>
    </div>
  </div>
</div>""".format(
        id=i, tr=track, st=round(start,2), dur=round(clip_dur,2),
        label=label_text, img=img_path, bg=bg_style, sp=16, spans=word_spans
    )
    
    word_json = json.dumps(word_arr)
    
    tl_js += """
  tl.set("#p{id}",{{opacity:1}},{st});
  tl.from("#p{id} .img-wrap",{{opacity:0,y:30,scale:.95,duration:.5,ease:\"power2.out\"}},{st2});
  tl.from("#p{id} .cap",{{opacity:0,y:16,duration:.35,ease:\"power2.out\"}},{st3});
  tl.from("#p{id} .label",{{opacity:0,y:-8,duration:.25}},{st});
  (function(){{
    var words = {wj};
    words.forEach(function(w,idx){{
      tl.call(function(){{
        var els = document.querySelectorAll('#p{id} .kw.active');
        for(var e=0;e<els.length;e++){{els[e].classList.remove('active');els[e].classList.add('done')}};
        var el = document.querySelector('#p{id} .kw[data-w=\"'+idx+'\"]');
        if(el) el.classList.add('active');
      }},{},w.s);
      tl.call(function(){{
        var el = document.querySelector('#p{id} .kw[data-w=\"'+idx+'\"]');
        if(el){{el.classList.remove('active');el.classList.add('done')}}
      }},{},w.e);
    }});
  }})();
  tl.to('#p{id}',{{opacity:0,duration:.25}},{end});""".format(
        id=i, st=round(start,2), st2=round(start+0.15,2), st3=round(start+0.45,2),
        end=round(start+clip_dur-0.25,2), wj=word_json
    )
    
    phrases.append({"start": start, "end": start + clip_dur, "text": display[:80]})

# Render template
with open(TEMPLATE) as f:
    html = f.read()

html = html.replace("__DUR__", "{:.1f}".format(total_dur))
html = html.replace("__PHASES__", phases_html)
html = html.replace("__PHRASES__", json.dumps(phrases))
html = html.replace("__TL__", tl_js)

out = BASE / "index.html"
with open(out, "w") as f:
    f.write(html)

print("Written {out}".format(out=out))
print("Phases: {n}".format(n=len(segments)))
img_count = sum(1 for p in range(len(segments)) if (BASE / "images/seg_{:02d}.png".format(p)).exists())
print("Images: {c}/{t}".format(c=img_count, t=len(segments)))
print("Duration: {d:.1f}s".format(d=total_dur))