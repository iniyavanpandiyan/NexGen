#!/usr/bin/env python3
"""Regenerate a small subset of segment images for a slug with tight prompts."""
import argparse, json, os, sys, time, shutil, subprocess, urllib.request, urllib.error
from PIL import Image
import numpy as np

ROOT = "/home/fiipadmin/workspace/NexGen"
COMFY = "http://127.0.0.1:8188"
COMFY_OUT = "/home/fiipadmin/comfy/ComfyUI/output"

PROMPTS = {
    11: "a single ice cube with small wavy steam lines rising above it",
    16: "three simple symbols in a row: a square, a wavy line, three dots",
}

STYLE = (" flat single object, plain cream background, cobalt blue line drawing, "
         "minimal vector, no other colors, no text")
NEG = ("text, letters, words, numbers, person, people, photo, realistic, "
       "colorful, multiple colors, scenery, landscape, grey, black")


def comfy_up():
    try:
        with urllib.request.urlopen(f"{COMFY}/system_stats", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def post(payload):
    for _ in range(3):
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(COMFY + "/prompt", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print("[warn]", e, file=sys.stderr); time.sleep(4)
    raise RuntimeError("post failed")


def history(pid, timeout=240):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            with urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10) as r:
                d = json.loads(r.read().decode())
        except Exception:
            time.sleep(2); continue
        if pid in d:
            return d[pid]
        time.sleep(2)
    raise TimeoutError("timeout")


def graph(prompt, seed):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
        "2": {"class_type": "CLIPTextEncodeSDXL", "inputs": {"width": 768, "height": 768, "crop_w": 0, "crop_h": 0, "target_width": 768, "target_height": 768, "text_g": prompt + STYLE, "clip": ["1", 1], "text_l": "flat vector icon"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 768, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 28, "cfg": 7.0, "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0, "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "cbse", "images": ["6", 0]}},
    }


def recolor(img, cobalt=(26, 63, 176), cream=(244, 241, 234)):
    a = np.asarray(img.convert("RGB")).astype("float32")
    lum = a[:, :, 0]*0.2126 + a[:, :, 1]*0.7152 + a[:, :, 2]*0.0722
    mask = (lum < 0.62*255.0).astype("float32")[:, :, None]
    c = np.array(cobalt, "float32").reshape(1, 1, 3)
    k = np.array(cream, "float32").reshape(1, 1, 3)
    return Image.fromarray((mask*c + (1-mask)*k).astype("uint8"), "RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--idx", type=int, required=True, help="segment index to regen")
    args = ap.parse_args()
    if args.idx not in PROMPTS:
        print("no tight prompt for", args.idx); sys.exit(1)
    if not comfy_up():
        print("ComfyUI down"); sys.exit(2)
    slug_dir = os.path.join(ROOT, "pipeline", "rendered", args.slug)
    prefix = f"cbse_{args.slug}_{args.idx:02d}"
    g = graph(PROMPTS[args.idx], 9000 + args.idx)
    g["7"]["inputs"]["filename_prefix"] = prefix
    resp = post({"prompt": g, "client_id": f"cbse-{args.slug}"})
    h = history(resp["prompt_id"])
    fname = h["outputs"]["7"]["images"][0]["filename"]
    src = os.path.join(COMFY_OUT, fname)
    dst = os.path.join(slug_dir, "images", f"seg_{args.idx:02d}.png")
    if os.path.exists(src):
        im = recolor(Image.open(src))
        im.save(dst)
        print(f"[ok] regen seg {args.idx} -> {dst}")
    else:
        print("[err] file missing", src)


if __name__ == "__main__":
    sys.exit(main())
