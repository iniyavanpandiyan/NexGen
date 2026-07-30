"""Generate a faceless explainer HTML composition from existing script + manifest data.
Reads the built slug dir and generates a fresh index.html using the
faceless-explainer template, with proper karaoke data and image placeholders.
"""
import json, os, re, sys
from pathlib import Path

SLUG = "c9-exploration-ch1"
BASE = Path(__file__).resolve().parent.parent / "pipeline" / "rendered" / SLUG
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "pipeline" / "templates" / "faceless-explainer.html"

def load_manifest():
    with open(BASE / "manifest.json") as f:
        return json.load(f)

def load_words():
    """Load per-word timestamps from the manifest's word_alignment data."""
    manifest = load_manifest()
    segments = manifest.get("segments", [])
    word_data = manifest.get("word_alignment", [])
    if isinstance(word_data, list) and len(word_data) > 0:
        return word_data  # list of {word, start, end, segment}
    # Fallback: try transcript.json
    tx_path = BASE / "transcript.json"
    if tx_path.exists():
        with open(tx_path) as f:
            return json.load(f)
    return []

def generate_phases():
    manifest = load_manifest()
    segments = manifest.get("segments", [])
    words = load_words()
    
    # Group words by segment
    seg_words = {}
    for w in words:
        seg_idx = w.get("segment", 0)
        if seg_idx not in seg_words:
            seg_words[seg_idx] = []
        seg_words[seg_idx].append(w)
    
    phases_html = ""
    phases_js_words = ""
    phases_tl = ""
    
    for i, seg in enumerate(segments):
        seg_idx = seg.get("index", i)
        text = seg.get("text", "")
        start = seg.get("start", 0)
        duration = seg.get("duration", 5)
        seg_words_list = seg_words.get(seg_idx, [])
        
        # Clean text for display
        display_text = text
        for marker in ["[ACTIVITY]", "[THINK IT OVER]", "[DID YOU KNOW]", "[HINT]", "[READY TO GO BEYOND]"]:
            if marker in display_text:
                display_text = display_text.replace(marker, "").strip()
        
        # Generate word spans
        word_spans = ""
        js_word_array = []
        for j, w in enumerate(seg_words_list):
            word_text = w.get("w", w.get("word", ""))
            ws = w.get("s", w.get("start", 0))
            we = w.get("e", w.get("end", 0))
            word_spans += f'<span class="kw" data-w="{j}">{word_text}</span>\n        '
            js_word_array.append(f'{{"w":"{word_text}","s":{ws},"e":{we}}}')
        
        # If no word data, create a fallback
        if not seg_words_list:
            words_in_text = display_text.split()
            word_spans = ""
            js_word_array = []
            seg_duration = duration
            word_duration = seg_duration / max(len(words_in_text), 1)
            for j, w_text in enumerate(words_in_text):
                ws = start + j * word_duration
                we = start + (j + 1) * word_duration
                word_spans += f'<span class="kw" data-w="{j}">{w_text}</span>\n        '
                js_word_array.append(f'{{"w":"{w_text}","s":{ws:.3f},"e":{we:.3f}}}')
        
        # Determine if this is a special segment
        seg_class = ""
        tag = ""
        if "[ACTIVITY]" in text:
            tag = "🧪 Activity"
        elif "[THINK IT OVER]" in text:
            tag = "💭 Think It Over"
        elif "[DID YOU KNOW]" in text:
            tag = "💡 Did You Know?"
        elif "[HINT]" in text:
            tag = "💡 Hint"
        
        overline_text = f"Science · Class 9{f' · {tag}' if tag else ''}"
        
        # Image filename
        img_src = f"images/seg_{seg_idx:02d}.png"
        img_path = BASE / img_src
        if not img_path.exists():
            # Use a placeholder gradient
            img_src = ""
        
        phase_id = f"phase-{i+1}"
        # Duration: from this segment start to next segment start, or add 5s
        next_start = segments[i+1]["start"] if i+1 < len(segments) else start + duration + 1
        phase_duration = next_start - start
        
        phases_html += f'''
  <div id="{phase_id}" class="phase" data-track-index="{i}" data-start="{start}" data-duration="{phase_duration}">
    <div class="phase-content">
      <div class="overline">{overline_text}</div>
      <div class="image-card" id="img-{i+1}">
        <img src="{img_src}" alt="" id="seg-img-{seg_idx}" />
        <div class="image-frame"></div>
        <div class="image-glow"></div>
      </div>
      <div class="caption-pill" id="cap-{i+1}">
        {word_spans}
      </div>
    </div>
  </div>'''
        
        phases_js_words += f'''
  // Phase {i+1}: {display_text[:60]}...
  const phase{i+1}Words = [{", ".join(js_word_array)}];'''
        
        phases_tl += f'''
  // Phase {i+1}: {start}s - {start+phase_duration}s
  tl.set("#{phase_id}", {{ opacity: 1 }}, {start});
  tl.from("#{phase_id} .image-card", {{ opacity: 0, y: 40, scale: 0.95, duration: 0.5, ease: "power2.out" }}, {start + 0.1});
  tl.from("#{phase_id} .caption-pill", {{ opacity: 0, y: 20, duration: 0.35, ease: "power2.out" }}, {start + 0.4});
  tl.from("#{phase_id} .overline", {{ opacity: 0, y: -10, duration: 0.25, ease: "power2.out" }}, {start});
  // Karaoke
  phase{i+1}Words.forEach(function(w, idx) {{
    tl.call(function() {{
      document.querySelectorAll('#{phase_id} .kw.is-active').forEach(function(el) {{ el.classList.remove('is-active'); el.classList.add('is-spoken'); }});
      var kw = document.querySelector('#{phase_id} .kw[data-w="' + idx + '"]');
      if (kw) kw.classList.add('is-active');
    }}, [], w.s);
    tl.call(function() {{
      var kw = document.querySelector('#{phase_id} .kw[data-w="' + idx + '"]');
      if (kw) {{ kw.classList.remove('is-active'); kw.classList.add('is-spoken'); }}
    }}, [], w.e);
  }});
  // Fade out at end
  tl.to("#{phase_id}", {{ opacity: 0, duration: 0.3, ease: "power2.in" }}, {start + phase_duration - 0.3});'''
    
    total_duration = segments[-1]["start"] + segments[-1].get("duration", 5) + 1 if segments else 0
    
    return phases_html, phases_js_words, phases_tl, total_duration

def main():
    phases_html, phases_js_words, phases_tl, total_duration = generate_phases()
    
    with open(TEMPLATE_PATH) as f:
        template = f.read()
    
    # Replace placeholders
    html = template.replace("__VIDEO_DURATION__", str(total_duration))
    
    # Insert phases before the progress bar
    html = html.replace("<!-- more phases will be generated by the pipeline -->", phases_html)
    
    # Replace the word data
    word_start = html.find("const WORDS")
    word_end = html.find(";", word_start) + 1
    html = html[:word_start] + "const WORDS = [];" + html[word_end:]
    
    # Insert phase word data and timeline
    script_end = html.find("window.__timelines")
    js_insert = phases_js_words + "\n  \n  " + phases_tl + "\n  "
    html = html[:script_end] + js_insert + html[script_end:]
    
    # Write the output
    out_path = BASE / "index.html"
    with open(out_path, "w") as f:
        f.write(html)
    
    print(f"Generated {out_path}")
    print(f"Total duration: {total_duration:.1f}s")
    print(f"Phases: {len(phases_html.split('data-track-index=')) - 1}")

if __name__ == "__main__":
    main()