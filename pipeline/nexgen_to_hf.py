#!/usr/bin/env python3
"""
nexgen_to_hf.py — NexGen script → HyperFrames → Telegram delivery.

One-command wrapper: takes a NexGen-format script list (or script.json path),
runs TTS + SVG-scene HTML generation, hyperframes check/render/finalize,
and sends the final MP4 via Telegram.

Usage:
  python3 nexgen_to_hf.py --slug my-video --template nebula \\
      --title "Cell Biology" --subject Biology --class "Class 9" \\
      '[
        {"text":"Cells are the basic unit of life","icon":"leaf"},
        {"text":"The nucleus controls all activities","icon":"atom"}
      ]'

Or from a script.json on disk:
  python3 nexgen_to_hf.py --slug my-video --template nebula --script path/to/script.json
"""

import argparse, json, os, subprocess, sys, time, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "pipeline", "lib")
RENDERED = os.path.join(ROOT, "pipeline", "rendered")


def esc(s):
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def run(cmd, cwd=None, capture=True):
    cwd = cwd or ROOT
    print(f"[run] {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=capture, text=True, cwd=cwd, timeout=300)
    if r.returncode != 0 and capture:
        print(f"[FAIL] {r.stderr[:500]}", file=sys.stderr)
        sys.exit(r.returncode)
    return r


def segments_to_script(slug, segments, title, subject, cls, duration):
    segs = []
    t = 0.0
    n = len(segments)
    per = duration / n if n else 6
    for i, s in enumerate(segments):
        segs.append({
            "index": i,
            "text": s.get("text", ""),
            "start": round(t, 2),
            "duration": round(s.get("duration", per), 2),
            "image": s.get("image", ""),
            "icon": s.get("icon", s.get("icon", "")),
            "image_prompt": s.get("image_prompt", ""),
        })
        t += segs[-1]["duration"]
    return {
        "title": title,
        "subject": subject,
        "class": cls,
        "chapter": title,
        "slug": slug,
        "duration": round(t, 1),
        "segments": segs,
    }


def build_short(slug, template, tts, target):
    script = os.path.join(RENDERED, slug, "script.json")
    if not os.path.exists(script):
        sys.exit(f"[error] no script.json at {script}")
    cmd = [
        sys.executable, os.path.join(LIB, "build_short.py"),
        "--script", script,
        "--slug", slug,
        "--tts", tts,
        "--target-secs", str(target),
        "--template", template,
        "--svg-scenes",
    ]
    run(cmd)


def hyperframes_cmd(slug, *args):
    cmd = ["npx", "--yes", "hyperframes@0.7.76"] + list(args)
    cwd = os.path.join(RENDERED, slug)
    return run(cmd, cwd=cwd)


def finalize(slug):
    cmd = [sys.executable, os.path.join(LIB, "finalize.py"), "--slug", slug]
    return json.loads(run(cmd).stdout.strip())


def send_telegram(filepath, caption):
    env_path = os.path.expanduser("~/.config/opencode-telegram-bot/.env")
    token = None
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.strip().split("=", 1)[1]
                break
    if not token:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[warn] no TELEGRAM_BOT_TOKEN, skipping send")
        return
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "996288865")
    import http.client
    import mimetypes
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        body = f.read()
    payload = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode() + body + f"\r\n--{boundary}--\r\n".encode()
    conn = http.client.HTTPSConnection("api.telegram.org", timeout=30)
    conn.request("POST", f"/bot{token}/sendDocument", body=payload,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    resp = conn.getresponse()
    result = json.loads(resp.read())
    conn.close()
    if result.get("ok"):
        print(f"[telegram] sent — message_id {result['result']['message_id']}")
    else:
        print(f"[telegram] failed: {result.get('description', 'unknown')}")
    return result


def main():
    ap = argparse.ArgumentParser(description="NexGen → HyperFrames → Telegram")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--template", default="nebula")
    ap.add_argument("--tts", choices=["omnivoice", "edge"], default="omnivoice")
    ap.add_argument("--target-secs", type=float, default=30.0)
    ap.add_argument("--script", help="path to existing script.json")
    ap.add_argument("--title", default="CBSE Science")
    ap.add_argument("--subject", default="Science")
    ap.add_argument("--class", dest="cls", default="Class 10")
    ap.add_argument("--send", action="store_true", help="send final.mp4 via Telegram")
    ap.add_argument("segments", nargs="?", help="inline JSON array or path to NexGen scripts JSON")
    args = ap.parse_args()

    slug_dir = os.path.join(RENDERED, args.slug)
    script_path = os.path.join(slug_dir, "script.json")

    if args.script:
        import shutil
        os.makedirs(slug_dir, exist_ok=True)
        shutil.copy2(args.script, script_path)
    elif args.segments:
        # Read input: could be a file path or inline JSON
        seg_input = args.segments
        if os.path.exists(seg_input):
            with open(seg_input) as f:
                data = json.load(f)
        else:
            data = json.loads(seg_input)

        if isinstance(data, list):
            segments = data
        elif isinstance(data, dict) and "scripts" in data:
            segments = data["scripts"]
        elif isinstance(data, dict) and "segments" in data:
            segments = data["segments"]
        else:
            sys.exit("[error] unrecognized JSON format")

        os.makedirs(slug_dir, exist_ok=True)
        script = segments_to_script(args.slug, segments, args.title, args.subject, args.cls, args.target_secs)
        with open(script_path, "w") as f:
            json.dump(script, f, indent=2)
        print(f"[script] wrote {len(segments)} segments to {script_path}")
    else:
        sys.exit("[error] provide --script, --segments, or a JSON argument")

    # 1. TTS + HTML generation with SVG scenes
    t0 = time.time()
    build_short(args.slug, args.template, args.tts, args.target_secs)

    # 2. HyperFrames check
    hyperframes_cmd(args.slug, "check")

    # 3. Render
    hyperframes_cmd(args.slug, "render", "--quality", "draft", "--output", "silent.mp4")

    # 4. Finalize (mix audio)
    info = finalize(args.slug)
    t1 = time.time()

    # 5. Summary
    result = {
        "slug": args.slug,
        "template": args.template,
        "segments": len(json.load(open(script_path)).get("segments", [])),
        "duration_s": round(info.get("video_seconds", 0), 1),
        "size_mb": round(info.get("final_size", 0) / 1e6, 2),
        "elapsed_s": round(t1 - t0, 1),
        "final_mp4": info.get("final", ""),
    }
    print(json.dumps(result, indent=2))

    # 6. Optional Telegram delivery
    if args.send and info.get("final"):
        caption = (
            f"{args.template.title()} — {args.title}\n"
            f"{args.subject} · {args.cls} · {result['segments']} segments\n"
            f"{result['duration_s']}s · {result['size_mb']}MB · {result['elapsed_s']}s pipeline"
        )
        send_telegram(info["final"], caption)

    print(f"\nPreview: https://edu.fixitinpost.in/hyperframes/")
    print(f"Download: https://edu.fixitinpost.in/rendered/{args.slug}/final.mp4")


if __name__ == "__main__":
    main()
