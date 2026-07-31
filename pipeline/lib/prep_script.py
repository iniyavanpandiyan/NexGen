#!/usr/bin/env python3
"""
prep_script.py — turn a raw NCERT chapter explanation into a TTS-optimized,
fine-segmented, image-ready short script.

Pipeline:
  1. Read raw script text (--text file).
  2. Split into small chunks (~110 words) so the (free, reasoning-heavy) chat
     model can return complete JSON per chunk without hitting its output cap.
  3. For each chunk call the Nous chat model to:
       - PROOFREAD   (fix NCERT line-break splits: "Accor ding"->"According")
       - TTS-OPTIMIZE (expand e.g./i.e./etc, spell °C/%/-> for TTS)
       - BREAK DOWN  into short self-contained segments (one slide each,
         6-14 words, ends with a full stop)
       - IMAGE PROMPT per segment (ComfyUI/SDXL, flat editorial style matching
         the Cobalt Grid template: cream #f4f1ea + cobalt #1a3fb0, no text)
  4. Emit JSON -> pipeline/rendered/<slug>/script.json
     { title, subject, class, segments: [ {text, image_prompt} ] }

No API keys are hard-coded; the Nous access token is read from the Hermes
auth store at call time.
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error

ROOT = "/home/fiipadmin/workspace/NexGen"
AUTH = "/home/fiipadmin/.hermes/auth.json"
BASE_URL = "https://inference-api.nousresearch.com/v1"
CHAT_MODEL = "tencent/hy3:free"

SYSTEM = (
    "You are a JSON-only script formatter for an Indian NCERT exam-prep YouTube "
    "shorts channel. You receive ONE chunk of a rough chapter explanation and you "
    "return STRICT JSON. Do not explain. Do not plan out loud. Output the JSON object "
    "and nothing else.\n\n"
    "Rules:\n"
    "- PROOFREAD: NCERT text has line-break splits like 'Accor ding', 'fir e', "
    "'mate rial'. Merge them. Fix spelling/grammar. Keep correct terms "
    "(matter, latent heat, Dalton's atomic theory).\n"
    "- TTS-OPTIMIZE for a calm female English voice: 'e.g.'->'for example', "
    "'i.e.'->'that is', 'etc.'->'and so on', 'vs'->'versus', '°C'->'degree Celsius', "
    "'%'->'per cent', '->'->'to', '<'->'less than'. Add commas for breath pauses.\n"
    "- BREAK the chunk into 4 to 6 short segments. Each segment = ONE idea, "
    "6-14 words, ends with a full stop, self-contained and readable at a glance.\n"
    "- IMAGE PROMPT per segment: a ComfyUI/SDXL prompt for a clean FLAT editorial "
    "educational illustration for that segment. Style: minimal vector infographic, "
    "cream (#f4f1ea) background, cobalt blue (#1a3fb0) line-art and fills, soft "
    "off-white, academic, for a 9th-grade explainer. STRICTLY no text, no letters, "
    "no numbers in the image. Under 55 words.\n\n"
    "Output ONLY this JSON object (no markdown, no prose):\n"
    '{"segments":[{"text":"...","image_prompt":"..."}, ...]}'
)

USER_TMPL = (
    "Chapter: {subject}, Class {cls} — {title}.\n"
    "Chunk of the raw explanation (proofread, optimize, break into 4-6 segments, "
    "illustrate each):\n---\n{chunk}\n---\n\nReturn the STRICT JSON now."
)


def load_token():
    auth = json.load(open(AUTH))
    return auth["providers"]["nous"]["access_token"]


def chat(messages, max_tokens=4000, temperature=0.3):
    tok = load_token()
    url = BASE_URL + "/chat/completions"
    body = json.dumps({
        "model": CHAT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    })
    last = None
    for attempt in range(3):
        try:
            r = urllib.request.urlopen(req, timeout=180)
            data = json.loads(r.read().decode())
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning") or ""
            fr = data["choices"][0].get("finish_reason")
            # the free reasoning model sometimes routes the answer into 'reasoning'
            # or emits an empty 'content'; recover the best text blob.
            if "{" in content:
                best = content
            elif "{" in reasoning:
                best = reasoning
            else:
                best = content + "\n" + reasoning
            print(f"[llm] finish={fr} content={len(content)} reason={len(reasoning)}",
                  file=sys.stderr)
            return best
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 401 and attempt < 2:
                time.sleep(2)
                continue
            raise
    raise RuntimeError(f"chat failed: {last}")


def extract_json(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    s = t.find("{")
    e = t.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("no JSON object found")
    return t[s:e + 1]


def chunk_text(raw, target=110):
    """Split raw text into ~target-word chunks on sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", raw.strip())
    chunks, cur, n = [], [], 0
    for s in sentences:
        w = len(s.split())
        if cur and n + w > target:
            chunks.append(" ".join(cur))
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--class", dest="cls", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    raw = open(args.text, encoding="utf-8").read().strip()
    if not raw:
        print("[err] empty script", file=sys.stderr)
        sys.exit(1)

    chunks = chunk_text(raw)
    print(f"[prep] {len(chunks)} chunk(s) for '{args.title}'", file=sys.stderr)

    segments = []
    for ci, chunk in enumerate(chunks):
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TMPL.format(
                subject=args.subject, cls=args.cls, title=args.title, chunk=chunk)},
        ]
        print(f"[prep] chunk {ci+1}/{len(chunks)} -> Nous chat...", file=sys.stderr)
        out = chat(messages)
        try:
            obj = json.loads(extract_json(out))
        except Exception as e:
            print(f"[warn] chunk {ci+1} JSON parse failed ({e}); skipping",
                  file=sys.stderr)
            continue
        for s in obj.get("segments", []):
            s["text"] = re.sub(r"\s+", " ", s.get("text", "")).strip()
            s["image_prompt"] = re.sub(r"\s+", " ", s.get("image_prompt", "")).strip()
            if s["text"]:
                segments.append(s)

    if not segments:
        print("[err] no segments produced", file=sys.stderr)
        sys.exit(2)

    for i, s in enumerate(segments):
        s["index"] = i
    result = {
        "title": args.title,
        "subject": args.subject,
        "class": args.cls,
        "segments": segments,
    }
    out_path = args.out or os.path.join(
        ROOT, "pipeline", "rendered", args.slug, "script.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(result, open(out_path, "w"), indent=2, ensure_ascii=False)
    print(f"[prep] wrote {len(segments)} segments -> {out_path}", file=sys.stderr)
    print(json.dumps({"slug": args.slug, "segments": len(segments),
                      "title": result["title"]}))


if __name__ == "__main__":
    main()
