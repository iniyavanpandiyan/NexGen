#!/usr/bin/env python3
"""
orchestrate.py — full pipeline: script -> images -> TTS -> HTML -> render.

RTX 3060 12GB VRAM strategy:
  - --lowvram offloads model weights to CPU between prompts
  - Stages use different model families (Z-Image-Turbo vs OmniVoice vs Trellis2)
  - Restart ComfyUI between model families to guarantee zero VRAM conflict
  - tmux session keeps ComfyUI alive across shell timeouts
"""
import argparse, json, os, subprocess, sys, time, urllib.request, urllib.error

ROOT = "/home/fiipadmin/projects/cbse-youtube-channel"
LIB = os.path.join(ROOT, "pipeline", "lib")
COMFY_URL = "http://127.0.0.1:8188"
COMFY_DIR = "/home/fiipadmin/comfy/ComfyUI"
TMUX_SESSION = "comfy"


def comfy_running():
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def comfy_start():
    subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION],
                   capture_output=True, timeout=5)
    subprocess.run([
        "tmux", "new-session", "-d", "-s", TMUX_SESSION, "-c", COMFY_DIR
    ], capture_output=True, timeout=5)
    subprocess.run([
        "tmux", "send-keys", "-t", TMUX_SESSION,
        "./.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --lowvram", "Enter"
    ], capture_output=True, timeout=5)
    for _ in range(30):
        time.sleep(2)
        if comfy_running():
            return True
    return False


def comfy_restart():
    subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION],
                   capture_output=True, timeout=5)
    time.sleep(2)
    return comfy_start()


def ensure_comfy():
    if comfy_running():
        return True
    return comfy_start()


def run(step_name, cmd, cwd=None):
    print(f"[{step_name}] {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or ROOT, timeout=900)
    if r.returncode != 0:
        print(f"[FAIL] {step_name}:\n{r.stderr[:1000]}", file=sys.stderr)
        sys.exit(r.returncode)
    for line in r.stdout.strip().split("\n"):
        print(f"  {line}")
    try:
        return json.loads(r.stdout.strip().split("\n")[-1])
    except Exception:
        return {"raw": r.stdout.strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--steps", default="gen_images,tts",
                    help="comma-separated: gen_images,gen_3d,tts")
    ap.add_argument("--template", default="science")
    ap.add_argument("--tts", choices=["omnivoice", "edge"], default="omnivoice")
    ap.add_argument("--gen-3d", action="store_true")
    ap.add_argument("--target-secs", type=float, default=60.0)
    ap.add_argument("--svg-scenes", action="store_true",
                    help="use animated SVG inline diagrams instead of generated images")
    args = ap.parse_args()

    slug_dir = os.path.join(ROOT, "pipeline", "rendered", args.slug)
    script_path = os.path.join(slug_dir, "script.json")
    if not os.path.exists(script_path):
        sys.exit(f"[error] no script.json at {script_path}")

    steps = [s.strip() for s in args.steps.split(",")]
    if args.svg_scenes and "gen_images" in steps:
        steps.remove("gen_images")
    if args.svg_scenes and "tts" not in steps:
        steps.append("tts")
    t0 = time.time()

    if not ensure_comfy():
        sys.exit("[error] cannot start ComfyUI")

    model_family = None
    for step in steps:
        if step == "gen_images":
            if model_family and model_family != "zimage":
                print("[vram] switching model family: restarting ComfyUI", flush=True)
                if not comfy_restart():
                    sys.exit("[error] ComfyUI restart failed")
            model_family = "zimage"
            run("gen_images", [
                sys.executable, os.path.join(LIB, "gen_images.py"),
                "--slug", args.slug, "--supervisor", "--retries", "4"
            ])

        elif step == "gen_3d":
            if model_family and model_family != "trellis":
                print("[vram] switching to Trellis2: restarting ComfyUI", flush=True)
                if not comfy_restart():
                    sys.exit("[error] ComfyUI restart failed")
            model_family = "trellis"
            run("gen_3d", [
                sys.executable, os.path.join(LIB, "gen_3d.py"),
                "--slug", args.slug, "--supervisor", "--retries", "3"
            ])

        elif step == "tts":
            if model_family and model_family != "omnivoice":
                print("[vram] switching to OmniVoice: restarting ComfyUI", flush=True)
                if not comfy_restart():
                    sys.exit("[error] ComfyUI restart failed")
            model_family = "omnivoice"
            tts_cmd = [
                sys.executable, os.path.join(LIB, "build_short.py"),
                "--script", script_path,
                "--slug", args.slug,
                "--tts", args.tts,
                "--target-secs", str(args.target_secs),
                "--template", args.template,
            ]
            if args.svg_scenes:
                tts_cmd.append("--svg-scenes")
            run("tts", tts_cmd)
        else:
            print(f"[warn] unknown step '{step}'", file=sys.stderr)

    elapsed = time.time() - t0
    print(json.dumps({
        "slug": args.slug, "elapsed_seconds": round(elapsed, 1),
        "vram_strategy": "restart-between-model-families"
    }))


if __name__ == "__main__":
    main()
